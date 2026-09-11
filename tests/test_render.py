"""Il vincolo che rompe tutto su un telefono: nessuna riga oltre 32 colonne."""

import pytest

from ddos.engine import actions as A
from ddos.engine.actions import Action
from ddos.engine.dice import Roller
from ddos.engine.entities import ClassId, Status
from ddos.engine.reduce import reduce
from ddos.engine.state import new_game
from ddos.render import ansi
from ddos.render.views import (
    end_view, log_view, map_view, monsters_view, party_view, pre, room_view,
    scene_view, sheet_view,
)
from tests.test_fuzz import _azione_casuale

CLASSI = list(ClassId)


def partita(seed, azioni=0):
    r = Roller(f"scelte-{seed}")
    st = new_game("g", seed)
    for i in range(4):
        st, _ = reduce(st, Action(A.JOIN, f"pg{i+1}", f"Personaggio{i+1}", str(CLASSI[i])))
    st, eventi = reduce(st, Action(A.BEGIN))
    for _ in range(azioni):
        if st.over:
            break
        st, eventi = reduce(st, _azione_casuale(st, r))
    return st, eventi


# --- primitive -------------------------------------------------------------


def test_box_rispetta_la_larghezza():
    righe = ansi.box(["x" * 200, "corto", ""], "UN TITOLO ESAGERATAMENTE LUNGO DAVVERO")
    assert all(len(r) == ansi.WIDTH for r in righe)


def test_wrap_non_supera_mai_la_larghezza():
    testo = "parolina " * 30 + "UnaParolaLunghissimaCheNonStaInUnaRigaDaSola " * 3
    assert all(len(r) <= ansi.INNER for r in ansi.wrap(testo))


def test_wrap_non_perde_parole():
    testo = "il ladro apre la porta e trova una trappola"
    assert " ".join(ansi.wrap(testo, 12)).split() == testo.split()


@pytest.mark.parametrize("cur,mx", [(0, 10), (1, 10), (10, 10), (5, 10), (0, 0), (3, 3)])
def test_barra_lunghezza_costante(cur, mx):
    assert len(ansi.bar(cur, mx, 8)) == 10


def test_barra_piena_solo_a_pf_pieni():
    assert ansi.bar(10, 10, 8).count(ansi.FULL) == 8
    assert ansi.bar(9, 10, 8).count(ansi.FULL) < 8


def test_barra_mostra_un_blocco_finche_si_respira():
    assert ansi.bar(1, 100, 8).count(ansi.FULL) == 1
    assert ansi.bar(0, 100, 8).count(ansi.FULL) == 0


def test_kv_allinea_a_destra():
    riga = ansi.kv("Oro", "1234")
    assert riga.endswith("1234")
    assert len(riga) == ansi.INNER


def test_clip_non_allunga_mai():
    assert len(ansi.clip("x" * 100, 10)) == 10
    assert ansi.clip("breve", 10) == "breve"


# --- schermate su partite vere ---------------------------------------------


@pytest.mark.parametrize("seed", [f"RENDER-{i}" for i in range(12)])
@pytest.mark.parametrize("azioni", [0, 15, 60])
def test_nessuna_schermata_sfonda_le_32_colonne(seed, azioni):
    st, eventi = partita(seed, azioni)
    schermate = [scene_view(st, eventi), map_view(st), party_view(st), log_view(eventi)]
    if st.room is not None:
        schermate.append(room_view(st))
    if st.combat is not None:
        schermate.append(monsters_view(st))
    if st.over:
        schermate.append(end_view(st))
    for ch in st.party:
        schermate.append(sheet_view(ch))
    for schermata in schermate:
        for riga in schermata:
            assert len(riga) <= ansi.WIDTH, f"{len(riga)} colonne: {riga!r}"


def test_la_scheda_regge_i_casi_limite():
    st, _ = partita("LIMITI", 10)
    ch = st.party[0]
    ch.name = "Nome Assurdamente Lungo Davvero"
    ch.inventory = {k: 99 for k in ("gemma", "pozione_cura", "lama_runica", "usbergo_nano")}
    ch.status = Status.MORENTE
    ch.hp = 0
    for riga in sheet_view(ch):
        assert len(riga) <= ansi.WIDTH


def test_il_log_taglia_la_coda_non_la_testa():
    st, _ = partita("LOG", 5)
    from ddos.engine.events import ev

    eventi = [ev("info", f"riga numero {i}") for i in range(40)]
    righe = log_view(eventi, limit=5)
    assert any("39" in r for r in righe)
    assert not any("riga numero 0" in r for r in righe)


def test_i_sussurri_non_finiscono_nel_log_pubblico():
    from ddos.engine.events import ev

    righe = log_view([ev("sussurro", "c'e' un ogre dietro la porta", to="pg2")])
    assert not any("ogre" in r for r in righe)


def test_pre_produce_html_valido_per_telegram():
    testo = pre(["<script>", "a & b", "10 > 3"])
    assert testo.startswith("<pre>") and testo.endswith("</pre>")
    assert "&lt;script&gt;" in testo and "&amp;" in testo
    assert "<script>" not in testo


def test_la_mappa_mostra_solo_cio_che_si_conosce():
    st, _ = partita("MAPPA", 0)
    righe = map_view(st)
    visibili = sum(riga.count("?") + riga.count("@") for riga in righe)
    assert visibili <= len(st.level.rooms)
    assert any("@" in riga for riga in righe)
