"""Il livello Telegram, con un finto bot che registra le chiamate.

Qui si verificano le cose che non si vedono giocando da soli: due tap
simultanei, una tastiera rimasta aperta, un segreto che non deve finire nel
gruppo, un turno lasciato scadere.
"""

import asyncio
from dataclasses import dataclass, field

import pytest

from ddos.bot import keyboards as KB
from ddos.bot.session import GameService, TIMEOUT
from ddos.engine import actions as A
from ddos.engine.actions import Action
from ddos.engine.entities import ClassId, Status
from ddos.engine.state import Phase
from ddos.store.repo import Repo

CHAT = -1001234
UTENTI = {"marco": 101, "lia": 102, "ugo": 103, "dani": 104}


class TelegramBadRequestFinto(Exception):
    pass


@dataclass
class MessaggioFinto:
    message_id: int
    chat_id: int
    text: str


@dataclass
class BotFinto:
    """Registra tutto quello che il servizio manderebbe a Telegram."""

    inviati: list[MessaggioFinto] = field(default_factory=list)
    modifiche: list[tuple[int, int, str]] = field(default_factory=list)
    cancellati: list[tuple[int, int]] = field(default_factory=list)
    fissati: list[int] = field(default_factory=list)
    privati_bloccati: set[int] = field(default_factory=set)
    _prossimo_id: int = 1000

    async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
        if chat_id in self.privati_bloccati:
            raise TelegramBadRequestFinto("bot bloccato")
        self._prossimo_id += 1
        messaggio = MessaggioFinto(self._prossimo_id, chat_id, text)
        self.inviati.append(messaggio)
        return messaggio

    async def edit_message_text(self, chat_id, message_id, text, reply_markup=None, **kw):
        self.modifiche.append((chat_id, message_id, text))
        return MessaggioFinto(message_id, chat_id, text)

    async def delete_message(self, chat_id, message_id):
        self.cancellati.append((chat_id, message_id))

    async def pin_chat_message(self, chat_id, message_id, **kwargs):
        self.fissati.append(message_id)

    async def unpin_chat_message(self, chat_id, message_id):
        pass

    # --- comodita' per i test ---
    def testi_in(self, chat_id: int) -> list[str]:
        return [m.text for m in self.inviati if m.chat_id == chat_id]

    @property
    def testo_gruppo(self) -> str:
        return "\n".join(self.testi_in(CHAT))


@pytest.fixture
def ambiente(tmp_path):
    repo = Repo(tmp_path / "bot.db")
    asyncio.run(repo.setup())
    bot = BotFinto()
    return bot, repo, GameService(bot, repo)


async def _compagnia(service, repo, classi=("guerriero", "ladro", "mago", "chierico")):
    await repo.create_game(CHAT, "SEME-BOT")
    nomi = list(UTENTI)
    for nome, classe in zip(nomi, classi):
        await service.apply(CHAT, Action(A.JOIN, target=nome.capitalize(), value=classe),
                            telegram_id=UTENTI[nome], username=nome)
    return await repo.active_game(CHAT)


# --- attribuzione ----------------------------------------------------------


def test_il_bottone_non_impersona_nessuno(ambiente):
    bot, repo, service = ambiente

    async def scenario():
        riga = await _compagnia(service, repo)
        assert [c.name for c in riga.state.party] == ["Marco", "Lia", "Ugo", "Dani"]
        # Il personaggio giusto e' legato al telegram_id giusto.
        assert await repo.char_id_for(riga.id, UTENTI["lia"]) == riga.state.party[1].id

    asyncio.run(scenario())


def test_chi_non_gioca_non_agisce(ambiente):
    bot, repo, service = ambiente

    async def scenario():
        await _compagnia(service, repo)
        esito = await service.apply(CHAT, Action(A.SEARCH), telegram_id=999)
        assert not esito.ok
        assert "Non sei in questa spedizione" in esito.avviso

    asyncio.run(scenario())


def test_non_ci_si_iscrive_due_volte(ambiente):
    bot, repo, service = ambiente

    async def scenario():
        await _compagnia(service, repo)
        esito = await service.apply(CHAT, Action(A.JOIN, target="Bis", value="mago"),
                                    telegram_id=UTENTI["marco"])
        assert not esito.ok
        assert "gia'" in esito.avviso

    asyncio.run(scenario())


def test_muovere_passa_sempre_da_chi_guida(ambiente):
    bot, repo, service = ambiente

    async def scenario():
        riga = await _compagnia(service, repo)
        await service.apply(CHAT, Action(A.BEGIN), telegram_id=UTENTI["marco"])
        riga = await repo.active_game(CHAT)
        if riga.state.phase is not Phase.ESPLORAZIONE:
            pytest.skip("il seme parte in combattimento")
        direzione = sorted(riga.state.room.exits)[0]
        # Preme un gregario, ma l'azione viene attribuita al capo: nessun errore.
        esito = await service.apply(CHAT, Action(A.MOVE, value=direzione),
                                    telegram_id=UTENTI["ugo"])
        assert esito.ok

    asyncio.run(scenario())


# --- concorrenza -----------------------------------------------------------


def test_tastiera_scaduta_rifiutata(ambiente):
    bot, repo, service = ambiente

    async def scenario():
        riga = await _compagnia(service, repo)
        vecchio_seq = riga.state.turn_seq - 1
        esito = await service.apply(CHAT, Action(A.SEARCH), telegram_id=UTENTI["marco"],
                                    expected_seq=vecchio_seq)
        assert not esito.ok
        assert "scaduta" in esito.avviso

    asyncio.run(scenario())


def test_due_tap_simultanei_producono_una_sola_azione(ambiente):
    bot, repo, service = ambiente

    async def scenario():
        riga = await _compagnia(service, repo)
        await service.apply(CHAT, Action(A.BEGIN), telegram_id=UTENTI["marco"])
        riga = await repo.active_game(CHAT)
        seq = riga.state.turn_seq

        esiti = await asyncio.gather(
            service.apply(CHAT, Action(A.SEARCH), telegram_id=UTENTI["marco"],
                          expected_seq=seq),
            service.apply(CHAT, Action(A.SEARCH), telegram_id=UTENTI["marco"],
                          expected_seq=seq),
        )
        assert sum(e.ok for e in esiti) == 1, "il doppio tap ha agito due volte"
        finale = await repo.active_game(CHAT)
        assert finale.state.turn_seq == seq + 1

    asyncio.run(scenario())


def test_tap_diversi_in_sequenza_avanzano_il_turno(ambiente):
    bot, repo, service = ambiente

    async def scenario():
        await _compagnia(service, repo)
        await service.apply(CHAT, Action(A.BEGIN), telegram_id=UTENTI["marco"])
        for _ in range(3):
            riga = await repo.active_game(CHAT)
            prima = riga.state.turn_seq
            esito = await service.apply(CHAT, Action(A.SEARCH),
                                        telegram_id=UTENTI["marco"], expected_seq=prima)
            if esito.ok:
                dopo = await repo.active_game(CHAT)
                assert dopo.state.turn_seq == prima + 1

    asyncio.run(scenario())


# --- segreti ---------------------------------------------------------------


def test_il_sussurro_del_ladro_non_finisce_nel_gruppo(ambiente):
    bot, repo, service = ambiente

    async def scenario():
        await _compagnia(service, repo)
        await service.apply(CHAT, Action(A.BEGIN), telegram_id=UTENTI["marco"])
        riga = await repo.active_game(CHAT)
        if riga.state.phase is not Phase.ESPLORAZIONE:
            pytest.skip("il seme parte in combattimento")

        for _ in range(40):
            riga = await repo.active_game(CHAT)
            if riga.state.phase is not Phase.ESPLORAZIONE:
                break
            direzione = sorted(riga.state.room.exits)[0]
            esito = await service.apply(CHAT, Action(A.SCOUT, value=direzione),
                                        telegram_id=UTENTI["lia"])
            sussurri = [e for e in esito.eventi if e.kind == "sussurro"]
            if sussurri:
                segreto = sussurri[0].text
                privati = bot.testi_in(UTENTI["lia"])
                assert any(segreto in t for t in privati), "il segreto non e' arrivato"
                assert segreto not in bot.testo_gruppo, "il segreto e' trapelato nel gruppo!"
                return
        pytest.skip("l'avanscoperta non e' mai riuscita con questo seme")

    asyncio.run(scenario())


def test_senza_chat_privata_il_gruppo_non_scopre_il_segreto(ambiente):
    bot, repo, service = ambiente

    async def scenario():
        await _compagnia(service, repo)
        await service.apply(CHAT, Action(A.BEGIN), telegram_id=UTENTI["marco"])
        bot.privati_bloccati.add(UTENTI["lia"])

        for _ in range(40):
            riga = await repo.active_game(CHAT)
            if riga.state.phase is not Phase.ESPLORAZIONE:
                break
            direzione = sorted(riga.state.room.exits)[0]
            esito = await service.apply(CHAT, Action(A.SCOUT, value=direzione),
                                        telegram_id=UTENTI["lia"])
            sussurri = [e for e in esito.eventi if e.kind == "sussurro"]
            if sussurri:
                assert sussurri[0].text not in bot.testo_gruppo
                assert "privato" in bot.testo_gruppo
                return
        pytest.skip("l'avanscoperta non e' mai riuscita con questo seme")

    asyncio.run(scenario())


# --- scadenze --------------------------------------------------------------


def test_il_timer_parte_solo_quando_si_aspetta_qualcuno(ambiente):
    bot, repo, service = ambiente

    async def scenario():
        await _compagnia(service, repo)
        riga = await repo.active_game(CHAT)
        assert riga.deadline is None, "in lobby non si aspetta nessuno"

        await service.apply(CHAT, Action(A.BEGIN), telegram_id=UTENTI["marco"])
        riga = await repo.active_game(CHAT)
        if riga.state.phase is Phase.COMBATTIMENTO:
            assert riga.deadline is not None
        else:
            assert riga.deadline is None

    asyncio.run(scenario())


def test_il_turno_scaduto_mette_in_guardia(ambiente):
    bot, repo, service = ambiente

    async def scenario():
        await _compagnia(service, repo)
        await service.apply(CHAT, Action(A.BEGIN), telegram_id=UTENTI["marco"])
        for _ in range(60):
            riga = await repo.active_game(CHAT)
            if riga.state.phase is Phase.COMBATTIMENTO:
                break
            if riga.state.over:
                pytest.skip("partita finita prima di un combattimento")
            direzione = sorted(riga.state.room.exits)[0]
            await service.apply(CHAT, Action(A.MOVE, value=direzione),
                                telegram_id=UTENTI["marco"])
        else:
            pytest.skip("nessun combattimento con questo seme")

        riga = await repo.active_game(CHAT)
        await repo.set_deadline(riga.id, 1.0)  # gia' scaduto
        assert await service.tick_scaduti() == 1
        dopo = await repo.active_game(CHAT)
        assert dopo.state.turn_seq > riga.state.turn_seq

    asyncio.run(scenario())


def test_il_ticker_ignora_le_partite_non_scadute(ambiente):
    bot, repo, service = ambiente

    async def scenario():
        await _compagnia(service, repo)
        riga = await repo.active_game(CHAT)
        await repo.set_deadline(riga.id, 2_000_000_000.0)
        assert await service.tick_scaduti() == 0

    asyncio.run(scenario())


# --- scena e chiusura ------------------------------------------------------


def test_la_scena_viene_modificata_non_riscritta(ambiente):
    bot, repo, service = ambiente

    async def scenario():
        riga = await _compagnia(service, repo)
        await service.redraw(riga)
        inviati_prima = len(bot.inviati)
        await service.apply(CHAT, Action(A.BEGIN), telegram_id=UTENTI["marco"])
        assert bot.modifiche, "la scena non e' stata aggiornata sul posto"
        nuovi = [m for m in bot.inviati[inviati_prima:] if m.chat_id == CHAT]
        assert all("Tocca a" in m.text for m in nuovi), \
            "la scena e' stata rimandata invece di essere modificata"

    asyncio.run(scenario())


def test_la_partita_persa_chiude_e_riempie_il_cimitero(ambiente):
    bot, repo, service = ambiente

    async def scenario():
        riga = await _compagnia(service, repo)
        await service.apply(CHAT, Action(A.BEGIN), telegram_id=UTENTI["marco"])
        riga = await repo.active_game(CHAT)

        for c in riga.state.party:
            c.hp = 0
            c.status = Status.MORTO
        await repo.save(riga.state, expected_turn_seq=riga.state.turn_seq)

        riga = await repo.active_game(CHAT)
        if riga.state.phase is Phase.COMBATTIMENTO:
            await service.apply(CHAT, Action(A.TICK, actor="__timeout__"))
        else:
            direzione = sorted(riga.state.room.exits)[0]
            await service.apply(CHAT, Action(A.MOVE, value=direzione),
                                telegram_id=UTENTI["marco"])

        assert await repo.active_game(CHAT) is None, "la partita non e' stata chiusa"
        assert len(await repo.graveyard(CHAT)) == 4
        assert "cimitero" in bot.testo_gruppo.lower()

    asyncio.run(scenario())


def test_nessuna_partita_nessuna_azione(ambiente):
    bot, repo, service = ambiente

    async def scenario():
        esito = await service.apply(CHAT, Action(A.SEARCH), telegram_id=UTENTI["marco"])
        assert not esito.ok
        assert "/nuova" in esito.avviso

    asyncio.run(scenario())


# --- tastiere --------------------------------------------------------------


def test_le_callback_stanno_nei_64_byte(ambiente):
    """Limite duro di Telegram: una callback piu' lunga viene rifiutata."""
    bot, repo, service = ambiente

    async def scenario():
        await _compagnia(service, repo)
        await service.apply(CHAT, Action(A.BEGIN), telegram_id=UTENTI["marco"])
        for _ in range(50):
            riga = await repo.active_game(CHAT)
            if riga.state.over:
                break
            for menu_aperto in ("", "inc", "zaino", "pod", "spia", "inc:cura"):
                markup = KB.scene_kb(riga.state, menu_aperto=menu_aperto)
                if markup is None:
                    continue
                for fila in markup.inline_keyboard:
                    for bottone in fila:
                        lunghezza = len(bottone.callback_data.encode("utf-8"))
                        assert lunghezza <= 64, f"{bottone.callback_data} = {lunghezza} byte"
            if riga.state.phase is Phase.COMBATTIMENTO:
                bersaglio = riga.state.live_monsters[0].id
                await service.apply(CHAT, Action(A.ATTACK, target=bersaglio),
                                    telegram_id=await _tg_di_turno(repo, riga))
            else:
                direzione = sorted(riga.state.room.exits)[0]
                await service.apply(CHAT, Action(A.MOVE, value=direzione),
                                    telegram_id=UTENTI["marco"])

    async def _tg_di_turno(repo, riga):
        return await repo.telegram_id_for(riga.id, riga.state.combat.current_id)

    asyncio.run(scenario())
