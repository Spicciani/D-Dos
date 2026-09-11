"""Il database deve garantire due cose: una partita per chat, e nessuna
scrittura che sovrascriva quella di un altro giocatore."""

import asyncio
import sqlite3

import pytest

from ddos.engine import actions as A
from ddos.engine.actions import Action
from ddos.engine.entities import ClassId
from ddos.engine.events import ev
from ddos.engine.reduce import reduce
from ddos.engine.state import new_game
from ddos.store import db
from ddos.store.repo import Repo, genera_seme


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    yield c
    c.close()


@pytest.fixture
def repo(tmp_path):
    r = Repo(tmp_path / "test.db")
    asyncio.run(r.setup())
    return r


def test_una_sola_partita_per_chat(conn):
    db.create_game(conn, -100, new_game("g1", "A"))
    with pytest.raises(sqlite3.IntegrityError):
        db.create_game(conn, -100, new_game("g2", "B"))


def test_dopo_la_fine_se_ne_puo_iniziare_unaltra(conn):
    db.create_game(conn, -100, new_game("g1", "A"))
    db.finish_game(conn, "g1")
    db.create_game(conn, -100, new_game("g2", "B"))
    assert db.active_game(conn, -100).id == "g2"


def test_chat_diverse_non_si_disturbano(conn):
    db.create_game(conn, -100, new_game("g1", "A"))
    db.create_game(conn, -200, new_game("g2", "B"))
    assert db.active_game(conn, -100).id == "g1"
    assert db.active_game(conn, -200).id == "g2"


def test_scrittura_concorrente_rifiutata(conn):
    st = new_game("g1", "A")
    db.create_game(conn, -100, st)
    st.turn_seq = 1
    db.save_game(conn, st, expected_turn_seq=0)
    st.turn_seq = 2
    with pytest.raises(db.ConflittoDiVersione):
        db.save_game(conn, st, expected_turn_seq=0)


def test_lo_stato_torna_indietro_identico(conn):
    st = new_game("g1", "SEME")
    st, _ = reduce(st, Action(A.JOIN, "pg1", "Grommash", str(ClassId.GUERRIERO)))
    st, _ = reduce(st, Action(A.BEGIN))
    db.create_game(conn, -100, st)
    riletta = db.active_game(conn, -100)
    assert riletta.state.to_dict() == st.to_dict()


def test_il_cimitero_non_duplica_i_caduti(conn):
    st = new_game("g1", "SEME")
    st, _ = reduce(st, Action(A.JOIN, "pg1", "Grommash", str(ClassId.GUERRIERO)))
    st.party[0].status = st.party[0].status.__class__.MORTO
    riga = db.create_game(conn, -100, st)
    assert db.bury_fallen(conn, riga) == 1
    db.bury_fallen(conn, riga)  # ripetuto: non deve duplicare
    assert len(db.graveyard(conn, -100)) == 1


def test_collegamento_giocatore_personaggio(conn):
    db.create_game(conn, -100, new_game("g1", "A"))
    db.link_player(conn, "g1", 777, "pg1", "marco")
    assert db.char_id_for(conn, "g1", 777) == "pg1"
    assert db.telegram_id_for(conn, "g1", "pg1") == 777
    db.link_player(conn, "g1", 777, "pg2", "marco")  # cambio personaggio
    assert db.char_id_for(conn, "g1", 777) == "pg2"


def test_le_scadenze_si_interrogano_per_tempo(conn):
    db.create_game(conn, -100, new_game("g1", "A"))
    db.set_deadline(conn, "g1", 1000.0)
    assert not db.expired_games(conn, 999.0)
    assert [r.id for r in db.expired_games(conn, 1001.0)] == ["g1"]
    db.finish_game(conn, "g1")
    assert not db.expired_games(conn, 1001.0)


def test_semi_leggibili_e_diversi():
    semi = {genera_seme() for _ in range(50)}
    assert len(semi) > 45
    for seme in semi:
        parola, _, numero = seme.partition("-")
        assert len(parola) == 6 and parola.isalpha() and parola.isupper()
        assert numero.isdigit()


# --- facciata asincrona ----------------------------------------------------


def test_repo_salva_e_rilegge(repo):
    async def scenario():
        riga = await repo.create_game(-100)
        st, eventi = reduce(riga.state, Action(A.JOIN, "pg1", "Silfa", str(ClassId.LADRO)))
        await repo.save(st, expected_turn_seq=riga.turn_seq, events=eventi)
        riletta = await repo.active_game(-100)
        assert [c.name for c in riletta.state.party] == ["Silfa"]
        assert riletta.turn_seq == st.turn_seq
        cronaca = await repo.recent_events(st.id)
        assert cronaca and "Silfa" in cronaca[0]["text"]

    asyncio.run(scenario())


def test_repo_rifiuta_la_scrittura_stantia(repo):
    async def scenario():
        riga = await repo.create_game(-100)
        primo, e1 = reduce(riga.state, Action(A.JOIN, "pg1", "A", str(ClassId.MAGO)))
        secondo, e2 = reduce(riga.state, Action(A.JOIN, "pg2", "B", str(ClassId.MAGO)))
        await repo.save(primo, expected_turn_seq=riga.turn_seq, events=e1)
        with pytest.raises(db.ConflittoDiVersione):
            await repo.save(secondo, expected_turn_seq=riga.turn_seq, events=e2)
        # La transazione fallita non deve lasciare eventi orfani.
        cronaca = await repo.recent_events(riga.id)
        assert all("B" != c["text"][0] for c in cronaca)

    asyncio.run(scenario())


def test_chiudere_una_partita_e_ripetibile(repo):
    async def scenario():
        riga = await repo.create_game(-100)
        st, _ = reduce(riga.state, Action(A.JOIN, "pg1", "Caduto", str(ClassId.MAGO)))
        st.party[0].status = st.party[0].status.__class__.MORTO
        await repo.save(st, expected_turn_seq=riga.turn_seq)
        aggiornata = await repo.active_game(-100)
        assert await repo.finish(aggiornata) == 1
        assert await repo.finish(aggiornata) == 1
        assert await repo.active_game(-100) is None
        assert len(await repo.graveyard(-100)) == 1

    asyncio.run(scenario())
