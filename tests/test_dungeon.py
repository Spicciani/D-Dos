import pytest

from ddos.engine import content as C
from ddos.engine.dice import Roller
from ddos.engine.dungeon import Level, RoomKind, generate_level, reachable_from

SEMI = ["ORCUS-4471", "ALFA", "BETA", "GAMMA", "DELTA", "SEME-CON-TRATTINI", "12345"]


@pytest.mark.parametrize("seed", SEMI)
@pytest.mark.parametrize("tier", [1, 2, 3])
def test_ogni_stanza_e_raggiungibile(seed, tier):
    livello = generate_level(Roller(seed), tier, boss_level=(tier == 3))
    assert reachable_from(livello) == set(livello.rooms)


@pytest.mark.parametrize("seed", SEMI)
def test_ingresso_e_uscita_sono_distinti_e_coerenti(seed):
    livello = generate_level(Roller(seed), 1)
    assert livello.entry_id != livello.exit_id
    assert livello.rooms[livello.entry_id].kind == RoomKind.INGRESSO
    assert livello.rooms[livello.exit_id].kind in (RoomKind.SCALA, RoomKind.BOSS)


@pytest.mark.parametrize("seed", SEMI)
def test_l_ingresso_non_e_mai_un_agguato(seed):
    """Nessuno deve morire al secondo zero: la stanza d'ingresso e' sgombra."""
    livello = generate_level(Roller(seed), 1)
    ingresso = livello.rooms[livello.entry_id]
    assert ingresso.cleared
    assert not ingresso.monster_kind
    assert not ingresso.trap


@pytest.mark.parametrize("seed", SEMI)
def test_l_uscita_e_la_stanza_piu_lontana(seed):
    from collections import deque

    livello = generate_level(Roller(seed), 2)
    dist, coda = {livello.entry_id: 0}, deque([livello.entry_id])
    while coda:
        corrente = coda.popleft()
        for vicino in livello.rooms[corrente].exits.values():
            if vicino not in dist:
                dist[vicino] = dist[corrente] + 1
                coda.append(vicino)
    assert dist[livello.exit_id] == max(dist.values())


def test_i_passaggi_sono_reciproci():
    from ddos.engine.dungeon import OPPOSITE

    livello = generate_level(Roller("RECIPROCI"), 1)
    for stanza in livello.rooms.values():
        for direzione, vicino_id in stanza.exits.items():
            assert livello.rooms[vicino_id].exits[OPPOSITE[direzione]] == stanza.id


@pytest.mark.parametrize("seed", SEMI)
def test_stesso_seme_stesso_livello(seed):
    a = generate_level(Roller(seed), 1)
    b = generate_level(Roller(seed), 1)
    assert a.to_dict() == b.to_dict()


def test_semi_diversi_livelli_diversi():
    a = generate_level(Roller("ALFA"), 1)
    b = generate_level(Roller("BETA"), 1)
    assert a.to_dict() != b.to_dict()


def test_il_boss_compare_solo_all_ultimo_livello():
    normale = generate_level(Roller("SEME"), 3, boss_level=False)
    finale = generate_level(Roller("SEME"), 3, boss_level=True)
    assert all(r.kind != RoomKind.BOSS for r in normale.rooms.values())
    boss = [r for r in finale.rooms.values() if r.kind == RoomKind.BOSS]
    assert len(boss) == 1
    assert C.BESTIARY[boss[0].monster_kind].boss_of == 3


@pytest.mark.parametrize("seed", SEMI)
def test_contenuti_coerenti_col_tipo_di_stanza(seed):
    livello = generate_level(Roller(seed), 2)
    for stanza in livello.rooms.values():
        if stanza.kind == RoomKind.MOSTRI:
            assert stanza.monster_kind in C.BESTIARY
            assert stanza.monster_count >= 1
            assert 2 in C.BESTIARY[stanza.monster_kind].tiers
        if stanza.kind == RoomKind.TRAPPOLA:
            assert stanza.trap in C.TRAPS
        if stanza.kind == RoomKind.TESORO:
            assert stanza.loot and all(k in C.ITEMS for k in stanza.loot)


def test_serializzazione_completa():
    livello = generate_level(Roller("SEME"), 1)
    assert Level.from_dict(livello.to_dict()).to_dict() == livello.to_dict()
