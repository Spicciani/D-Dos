"""Comandi slash.

I comandi sono la strada lenta: servono ad aprire e chiudere una spedizione e a
consultare le schede. Tutto quello che si fa durante il gioco passa dai bottoni.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

from ddos.bot import keyboards as KB
from ddos.bot.session import GameService
from ddos.engine import actions as A
from ddos.engine import content as C
from ddos.engine.actions import Action
from ddos.engine.entities import ClassId
from ddos.engine.state import PARTY_MAX
from ddos.render.views import esc, map_view, pre, sheet_view
from ddos.store.repo import Repo, genera_seme

log = logging.getLogger("ddos.comandi")
router = Router(name="comandi")

#: Nome scelto con `/entra <nome>` in attesa della classe. Vive quanto la lobby.
_nomi_in_sospeso: dict[tuple[int, int], str] = {}

ALIAS_CLASSI = {
    "guerriero": ClassId.GUERRIERO, "g": ClassId.GUERRIERO, "gue": ClassId.GUERRIERO,
    "ladro": ClassId.LADRO, "l": ClassId.LADRO, "lad": ClassId.LADRO,
    "mago": ClassId.MAGO, "m": ClassId.MAGO, "mag": ClassId.MAGO,
    "chierico": ClassId.CHIERICO, "c": ClassId.CHIERICO, "chi": ClassId.CHIERICO,
}

AIUTO = """<b>D-DOS</b> - dungeon crawl per compagnie

<b>Per cominciare</b>
/nuova [seme] - apri una spedizione in questo gruppo
/entra [nome] [classe] - crea il tuo personaggio
/via - si scende

<b>Durante il gioco</b>
I bottoni sotto la scena fanno tutto. Muove chi guida la compagnia;
in combattimento gioca chi ha l'iniziativa.

/scheda - la tua scheda, in privato
/mappa - la mappa del livello
/stato - ridisegna la scena
/capo &lt;nome&gt; - passa il comando
/cimitero - chi non e' tornato
/abbandona - chiudi la spedizione

<b>Le quattro classi</b>
""" + "\n".join(
    f"<b>{c.name}</b> - {esc(c.blurb)}" for c in C.CLASSES.values()
) + f"""

La compagnia arriva a {PARTY_MAX}. Ogni run ha un seme: due gruppi con lo
stesso seme scendono nello stesso sotterraneo.
Scrivimi /start in privato: schede e segreti arrivano di la'."""


def _in_gruppo(message: Message) -> bool:
    return message.chat.type in ("group", "supergroup")


@router.message(CommandStart(), F.chat.type.in_({"group", "supergroup"}))
@router.message(Command("aiuto", "help"))
async def aiuto(message: Message) -> None:
    await message.answer(AIUTO)


@router.message(Command("nuova"))
async def nuova(message: Message, command: CommandObject, service: GameService,
                repo: Repo) -> None:
    if not _in_gruppo(message):
        await message.answer("Una spedizione si apre in un gruppo: e' un gioco di compagnia.")
        return

    esistente = await repo.active_game(message.chat.id)
    if esistente is not None:
        await message.answer(
            "C'e' gia' una spedizione in corso in questo gruppo.\n"
            "Chiudetela con /abbandona, oppure /stato per ritrovare la scena.")
        return

    argomenti = (command.args or "").split()
    seme = argomenti[0].upper() if argomenti else genera_seme()
    modo = "lento" if len(argomenti) > 1 and argomenti[1].lower().startswith("lent") else "live"

    riga = await repo.create_game(message.chat.id, seme, modo)
    attesa = "6 ore" if modo == "lento" else "90 secondi"
    await message.answer(
        f"Nuova spedizione aperta.\nSeme: <code>{esc(seme)}</code>\n"
        f"Turno: {attesa} prima che il motore giochi al posto vostro.\n"
        f"Usate /entra per crearvi un personaggio.")
    await service.redraw(riga)


@router.message(Command("entra"))
async def entra(message: Message, command: CommandObject, service: GameService,
                repo: Repo) -> None:
    if not _in_gruppo(message):
        await message.answer("Si entra dal gruppo dove e' aperta la spedizione.")
        return
    riga = await repo.active_game(message.chat.id)
    if riga is None:
        await message.answer("Nessuna spedizione aperta. /nuova per cominciarne una.")
        return

    argomenti = (command.args or "").split()
    classe = None
    if argomenti and argomenti[-1].lower() in ALIAS_CLASSI:
        classe = ALIAS_CLASSI[argomenti.pop().lower()]
    nome = " ".join(argomenti).strip() or (message.from_user.first_name or "Anonimo")

    if classe is None:
        _nomi_in_sospeso[(message.chat.id, message.from_user.id)] = nome
        await message.answer(
            f"<b>{esc(nome)}</b>, che mestiere fai?",
            reply_markup=KB.class_kb(riga.state.turn_seq))
        return

    esito = await service.apply(
        message.chat.id, Action(A.JOIN, target=nome, value=str(classe)),
        telegram_id=message.from_user.id,
        username=message.from_user.username or message.from_user.first_name or "",
    )
    if not esito.ok:
        await message.answer(esito.avviso)


def nome_in_sospeso(chat_id: int, user_id: int, ripiego: str) -> str:
    return _nomi_in_sospeso.pop((chat_id, user_id), ripiego)


@router.message(Command("via", "comincia"))
async def via(message: Message, service: GameService) -> None:
    esito = await service.apply(message.chat.id, Action(A.BEGIN),
                                telegram_id=message.from_user.id)
    if not esito.ok:
        await message.answer(esito.avviso)


@router.message(Command("capo", "guida"))
async def capo(message: Message, command: CommandObject, service: GameService,
               repo: Repo) -> None:
    riga = await repo.active_game(message.chat.id)
    if riga is None:
        await message.answer("Nessuna spedizione in corso.")
        return
    cercato = (command.args or "").strip().lower()
    bersaglio = next(
        (c for c in riga.state.party
         if c.name.lower() == cercato or c.name.lower().startswith(cercato)),
        None) if cercato else None
    if bersaglio is None:
        nomi = ", ".join(c.name for c in riga.state.party) or "nessuno"
        await message.answer(f"Chi deve guidare? Compagnia: {esc(nomi)}")
        return
    esito = await service.apply(message.chat.id,
                                Action(A.SET_LEADER, target=bersaglio.id),
                                telegram_id=message.from_user.id)
    if not esito.ok:
        await message.answer(esito.avviso)


@router.message(Command("stato", "scena"))
async def stato(message: Message, service: GameService, repo: Repo) -> None:
    riga = await repo.active_game(message.chat.id)
    if riga is None:
        await message.answer("Nessuna spedizione in corso. /nuova per aprirne una.")
        return
    # Una scena nuova invece di modificare la vecchia: cosi' risale in fondo alla chat.
    riga.scene_message_id = None
    await repo.set_scene_message(riga.id, None)
    await service.redraw(riga)


@router.message(Command("mappa"))
async def mappa(message: Message, repo: Repo) -> None:
    riga = await repo.active_game(message.chat.id)
    if riga is None or riga.state.level is None:
        await message.answer("Non siete ancora sottoterra.")
        return
    await message.answer(pre(map_view(riga.state)))


@router.message(Command("scheda"))
async def scheda(message: Message, repo: Repo) -> None:
    if _in_gruppo(message):
        riga = await repo.active_game(message.chat.id)
    else:
        partite = await repo.active_games_for_player(message.from_user.id)
        riga = partite[0] if partite else None
    if riga is None:
        await message.answer("Non risulti in nessuna spedizione.")
        return

    char_id = await repo.char_id_for(riga.id, message.from_user.id)
    personaggio = riga.state.char(char_id) if char_id else None
    if personaggio is None:
        await message.answer("Non hai un personaggio in questa spedizione.")
        return

    try:
        await message.bot.send_message(message.from_user.id, pre(sheet_view(personaggio)))
        await repo.mark_private_ok(message.from_user.id)
        if _in_gruppo(message):
            await message.answer(f"Scheda di {esc(personaggio.name)} mandata in privato.")
    except Exception:
        await message.answer(
            "Non riesco a scriverti in privato: aprimi una chat e fai /start.")


@router.message(Command("cimitero"))
async def cimitero(message: Message, repo: Repo) -> None:
    caduti = await repo.graveyard(message.chat.id)
    statistiche = await repo.hall_stats(message.chat.id)
    if not caduti:
        await message.answer("Il cimitero e' vuoto. Per ora.")
        return
    righe = [f"{c['name']} - {C.CLASSES[ClassId(c['cls'])].name} L{c['level']}, "
             f"caduto al livello {c['tier']} (seme {c['seed']})" for c in caduti]
    await message.answer(
        "<b>CIMITERO</b>\n" + esc("\n".join(righe)) +
        f"\n\n{statistiche['morti']} caduti in {statistiche['partite']} spedizioni. "
        f"Profondita' massima raggiunta: {statistiche['profondita_max']}.")


@router.message(Command("abbandona", "chiudi"))
async def abbandona(message: Message, service: GameService, repo: Repo) -> None:
    riga = await repo.active_game(message.chat.id)
    if riga is None:
        await message.answer("Nessuna spedizione da chiudere.")
        return
    async with service.lock(message.chat.id):
        caduti = await repo.finish(riga)
    await message.answer(
        f"Spedizione abbandonata. Seme <code>{esc(riga.state.seed)}</code> se volete "
        f"riprovarla." + (f"\n{caduti} caduti registrati." if caduti else ""))
