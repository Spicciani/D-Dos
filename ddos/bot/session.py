"""Il ponte tra Telegram e il motore.

Qui vive tutta la logica che i gestori dei comandi non devono ripetere:
prendere il lock della chat, caricare la partita, applicare l'azione, salvarla,
ridisegnare la scena e consegnare i segreti in privato.

Tre problemi di concorrenza, tre risposte:
  * due tap nello stesso istante -> un `asyncio.Lock` per chat;
  * un tap su una tastiera vecchia -> il numero di turno nella callback;
  * due processi del bot sulla stessa partita -> scrittura ottimistica su
    `turn_seq` nel database, che fallisce invece di sovrascrivere.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from ddos.bot import keyboards as KB
from ddos.engine import actions as A
from ddos.engine.actions import Action
from ddos.engine.events import Event
from ddos.engine.reduce import reduce
from ddos.engine.state import Phase
from ddos.render.views import esc, pre, scene_view
from ddos.store import db
from ddos.store.repo import Repo

log = logging.getLogger("ddos.session")

#: Secondi a disposizione per giocare il proprio turno.
TIMEOUT = {"live": 90.0, "lento": 6 * 3600.0}


@dataclass(slots=True)
class Esito:
    """Cosa dire a chi ha premuto il bottone, e cosa e' successo davvero."""

    ok: bool
    avviso: str = ""
    eventi: list[Event] = field(default_factory=list)
    riga: db.GameRow | None = None


class GameService:
    def __init__(self, bot: Bot, repo: Repo) -> None:
        self.bot = bot
        self.repo = repo
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._pings: dict[str, tuple[int, int]] = {}   # partita -> (chat, messaggio)
        self._ping_keys: dict[str, str] = {}           # partita -> turno gia' annunciato

    def lock(self, chat_id: int) -> asyncio.Lock:
        return self._locks[chat_id]

    # ----------------------------------------------------------------------
    # Applicazione di un'azione
    # ----------------------------------------------------------------------

    async def apply(
        self,
        chat_id: int,
        action: Action,
        *,
        telegram_id: int | None = None,
        expected_seq: int | None = None,
        username: str = "",
    ) -> Esito:
        async with self.lock(chat_id):
            riga = await self.repo.active_game(chat_id)
            if riga is None:
                return Esito(False, "Nessuna partita in corso. Prova /nuova.")

            if expected_seq is not None and expected_seq != riga.state.turn_seq:
                return Esito(False, "Azione scaduta: la scena e' gia' andata avanti.",
                             riga=riga)

            azione = await self._attribuisci(riga, action, telegram_id)
            if isinstance(azione, str):
                return Esito(False, azione, riga=riga)

            prima_seq = riga.state.turn_seq
            nuovo, eventi = reduce(riga.state, azione)

            if nuovo is riga.state:
                testo = eventi[0].text if eventi else "Azione non valida."
                return Esito(False, testo, riga=riga)

            if azione.kind == A.JOIN and telegram_id is not None:
                nuovo_pg = nuovo.party[-1]
                await self.repo.link_player(riga.id, telegram_id, nuovo_pg.id, username)

            try:
                await self.repo.save(nuovo, expected_turn_seq=prima_seq, events=eventi)
            except db.ConflittoDiVersione:
                return Esito(False, "Qualcun altro ha giocato un istante prima di te.",
                             riga=riga)

            riga.state = nuovo
            riga.turn_seq = nuovo.turn_seq
            await self._aggiorna_scadenza(riga)
            await self._consegna_sussurri(riga, eventi)
            await self.redraw(riga, eventi)

            if nuovo.over:
                await self._chiudi(riga)

            return Esito(True, eventi=eventi, riga=riga)

    async def _attribuisci(
        self, riga: db.GameRow, action: Action, telegram_id: int | None
    ) -> Action | str:
        """Decide chi agisce. Un bottone non impersona nessuno.

        Le chiamate interne senza `telegram_id` (il TICK del ticker) restano
        fidate. Tutto quello che arriva da una persona viene attribuito al suo
        personaggio; se la callback ne nomina uno, e' una *dichiarazione da
        verificare*, non un'autorizzazione.
        """
        if telegram_id is None:
            return action

        if action.actor:
            proprio = await self.repo.char_id_for(riga.id, telegram_id)
            if proprio != action.actor:
                dichiarato = riga.state.char(action.actor)
                nome = dichiarato.name if dichiarato else "un altro personaggio"
                return f"Questo tocca a {nome}: deve premere chi lo interpreta."
            return action

        if action.kind == A.JOIN:
            esistente = await self.repo.char_id_for(riga.id, telegram_id)
            if esistente and riga.state.char(esistente):
                return "Hai gia' un personaggio in questa spedizione."
            # Primo identificativo libero: se qualcuno esce, il suo posto si riusa.
            presi = {c.id for c in riga.state.party}
            nuovo_id = next(f"p{n}" for n in range(1, 32) if f"p{n}" not in presi)
            return Action(action.kind, nuovo_id, action.target, action.value)

        char_id = await self.repo.char_id_for(riga.id, telegram_id)
        if char_id is None:
            return "Non sei in questa spedizione. Usa /entra per unirti."

        # Il movimento e il riposo li decide chi guida, chiunque prema.
        if action.kind in (A.MOVE, A.REST, A.DESCEND) and riga.state.leader_id:
            char_id = riga.state.leader_id
        return Action(action.kind, char_id, action.target, action.value)

    # ----------------------------------------------------------------------
    # Disegno
    # ----------------------------------------------------------------------

    async def redraw(
        self, riga: db.GameRow, eventi: list[Event] | None = None, *, menu: str = ""
    ) -> None:
        testo = pre(scene_view(riga.state, eventi))
        markup = KB.scene_kb(riga.state, menu_aperto=menu)

        if riga.scene_message_id is not None:
            try:
                await self.bot.edit_message_text(
                    chat_id=riga.chat_id, message_id=riga.scene_message_id,
                    text=testo, reply_markup=markup,
                )
                await self._ping_di_turno(riga)
                return
            except TelegramBadRequest as exc:
                if "message is not modified" in str(exc):
                    return
                log.info("scena non modificabile (%s): ne mando una nuova", exc)

        messaggio = await self.bot.send_message(riga.chat_id, testo, reply_markup=markup)
        riga.scene_message_id = messaggio.message_id
        await self.repo.set_scene_message(riga.id, messaggio.message_id)
        try:
            await self.bot.pin_chat_message(riga.chat_id, messaggio.message_id,
                                            disable_notification=True)
        except (TelegramBadRequest, TelegramForbiddenError):
            pass  # senza permessi di amministratore si gioca lo stesso
        await self._ping_di_turno(riga)

    async def _ping_di_turno(self, riga: db.GameRow) -> None:
        """Una riga sola per chiamare chi tocca. La precedente viene cancellata."""
        stato = riga.state
        if stato.phase is not Phase.COMBATTIMENTO or stato.combat is None:
            await self._cancella_ping(riga.id)
            return
        attore = stato.char(stato.combat.current_id)
        if attore is None:
            return

        chiave = f"{stato.combat.current_id}:{stato.combat.round}"
        if self._ping_keys.get(riga.id) == chiave:
            return  # gia' annunciato: non richiamiamo la stessa persona due volte

        telegram_id = await self.repo.telegram_id_for(riga.id, attore.id)
        menzione = (f'<a href="tg://user?id={telegram_id}">{esc(attore.name)}</a>'
                    if telegram_id else esc(attore.name))
        await self._cancella_ping(riga.id)
        try:
            messaggio = await self.bot.send_message(
                riga.chat_id, f"Tocca a {menzione}.", disable_notification=False)
            self._pings[riga.id] = (riga.chat_id, messaggio.message_id)
            self._ping_keys[riga.id] = chiave
        except TelegramBadRequest:
            pass

    async def _cancella_ping(self, game_id: str) -> None:
        self._ping_keys.pop(game_id, None)
        posizione = self._pings.pop(game_id, None)
        if posizione is None:
            return
        chat_id, message_id = posizione
        try:
            await self.bot.delete_message(chat_id, message_id)
        except TelegramBadRequest:
            pass

    # ----------------------------------------------------------------------
    # Segreti, scadenze, chiusura
    # ----------------------------------------------------------------------

    async def _consegna_sussurri(self, riga: db.GameRow, eventi: list[Event]) -> None:
        """I sussurri vanno solo in privato. Il gruppo non deve nemmeno intuirli."""
        for evento in eventi:
            if evento.kind != "sussurro":
                continue
            char_id = evento.data.get("to", "")
            telegram_id = await self.repo.telegram_id_for(riga.id, char_id)
            personaggio = riga.state.char(char_id)
            nome = personaggio.name if personaggio else "il ladro"
            if telegram_id is None:
                await self.bot.send_message(
                    riga.chat_id,
                    f"{esc(nome)} ha scoperto qualcosa, ma non ho una chat privata "
                    f"con chi lo interpreta. Scrivetemi /start in privato.")
                continue
            try:
                await self.bot.send_message(telegram_id, pre([evento.text]))
                await self.repo.mark_private_ok(telegram_id)
            except Exception:
                # Qualunque motivo (bot bloccato, chat mai aperta, rete): il
                # turno e' gia' stato giocato e non si annulla per un messaggio.
                # Il gruppo viene avvisato, ma il contenuto resta segreto.
                log.info("sussurro non consegnato a %s", telegram_id, exc_info=True)
                await self.bot.send_message(
                    riga.chat_id,
                    f"{esc(nome)} ha una dritta da riferire, ma non posso scrivergli "
                    f"in privato: apra una chat con me e faccia /start.")

    async def _aggiorna_scadenza(self, riga: db.GameRow) -> None:
        """Il timer scatta solo quando la partita aspetta davvero un giocatore."""
        stato = riga.state
        attende = (
            stato.phase is Phase.COMBATTIMENTO
            and stato.combat is not None
            and stato.char(stato.combat.current_id) is not None
        )
        scadenza = time.time() + TIMEOUT.get(riga.turn_mode, 90.0) if attende else None
        riga.deadline = scadenza
        await self.repo.set_deadline(riga.id, scadenza)

    async def _chiudi(self, riga: db.GameRow) -> None:
        caduti = await self.repo.finish(riga)
        await self._cancella_ping(riga.id)
        try:
            if riga.scene_message_id:
                await self.bot.unpin_chat_message(riga.chat_id, riga.scene_message_id)
        except (TelegramBadRequest, TelegramForbiddenError):
            pass
        coda = (f"{caduti} nome/i inciso/i nel cimitero: /cimitero"
                if caduti else "Nessun caduto. Raro.")
        vinta = riga.state.phase is Phase.VITTORIA
        await self.bot.send_message(
            riga.chat_id,
            f"{'Spedizione conclusa con una vittoria.' if vinta else 'Spedizione finita.'}\n"
            f"{coda}\nSeme: <code>{esc(riga.state.seed)}</code> - /nuova {esc(riga.state.seed)} "
            f"per riprovare lo stesso sotterraneo.")

    # ----------------------------------------------------------------------
    # Timeout
    # ----------------------------------------------------------------------

    async def tick_scaduti(self) -> int:
        """Applica l'azione di default a chi ha lasciato scadere il turno."""
        scaduti = await self.repo.expired_games(time.time())
        for riga in scaduti:
            try:
                await self.apply(riga.chat_id, Action(A.TICK, actor="__timeout__"))
            except Exception:  # un errore su una partita non deve fermare le altre
                log.exception("tick fallito sulla partita %s", riga.id)
        return len(scaduti)


async def ticker(service: GameService, intervallo: float = 15.0) -> None:
    """Sorveglia le scadenze. Gira per tutta la vita del bot."""
    while True:
        try:
            await service.tick_scaduti()
        except Exception:
            log.exception("ciclo del ticker fallito")
        await asyncio.sleep(intervallo)
