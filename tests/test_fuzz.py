"""Partite automatiche a raffica: le invarianti non devono mai rompersi.

E' il test che ha piu' probabilita' di trovare i bug veri, perche' esplora
combinazioni che nessuno scriverebbe a mano.
"""

import json

import pytest

from ddos.engine import actions as A
from ddos.engine.actions import Action
from ddos.engine.dice import Roller
from ddos.engine.entities import ClassId, Status
from ddos.engine.reduce import reduce
from ddos.engine.state import GameState, Phase, new_game

CLASSI = list(ClassId)


def _verifica_invarianti(st: GameState) -> None:
    for c in st.party:
        assert 0 <= c.hp <= c.max_hp, f"{c.name}: {c.hp}/{c.max_hp}"
        assert c.max_hp >= 1
        assert 1 <= c.level <= 5
        assert 0 <= c.spell_slots <= max(c.spell_slots_max, 0)
        assert c.status in tuple(Status)
        assert (c.status is Status.VIVO) == (c.hp > 0) or c.status is not Status.VIVO
        assert all(v > 0 for v in c.conditions.values()), c.conditions
        assert all(q > 0 for q in c.inventory.values()), c.inventory
        assert c.death_failures <= 3 and c.death_successes <= 3
    for m in st.live_monsters:
        assert 0 < m.hp <= m.max_hp
    if st.combat is not None:
        assert st.phase is Phase.COMBATTIMENTO
        assert st.combat.order
        assert 0 <= st.combat.index < len(st.combat.order)
    if st.phase is Phase.COMBATTIMENTO:
        assert st.combat is not None
        # Il motore si ferma sempre su un giocatore che puo' agire.
        attore = st.char(st.combat.current_id)
        assert attore is not None and attore.alive, "turno fermo su chi non puo' agire"
    assert st.gold >= 0
    assert GameState.from_dict(json.loads(json.dumps(st.to_dict()))).to_dict() == st.to_dict()


def _azione_casuale(st: GameState, r: Roller) -> Action:
    if st.phase is Phase.COMBATTIMENTO:
        attore = st.char(st.combat.current_id)
        mostri = st.live_monsters
        scelte = [Action(A.DEFEND, attore.id), Action(A.TICK)]
        if mostri:
            bersaglio = r.choice(mostri).id
            scelte += [Action(A.ATTACK, attore.id, bersaglio),
                       Action(A.POWER, attore.id, bersaglio),
                       Action(A.CAST, attore.id, bersaglio, "dardo"),
                       Action(A.CAST, attore.id, bersaglio, "sonno"),
                       Action(A.CAST, attore.id, bersaglio, "scacciare")]
        scelte += [
            Action(A.HIDE, attore.id),
            Action(A.TAUNT, attore.id),
            Action(A.FLEE, attore.id),
            Action(A.CAST, attore.id, r.choice(st.party).id, "cura"),
            Action(A.CAST, attore.id, attore.id, "benedizione"),
            Action(A.USE, attore.id, r.choice(st.party).id, "pozione_cura"),
        ]
        return r.choice(scelte)

    attore = r.choice(st.standing_party or st.party)
    capo = st.leader_id
    uscite = sorted(st.room.exits) if st.room else []
    scelte = [Action(A.SEARCH, attore.id), Action(A.REST, capo),
              Action(A.TAKE, attore.id), Action(A.PRAY, attore.id),
              Action(A.DESCEND, capo), Action(A.DISARM, attore.id),
              Action(A.USE, attore.id, r.choice(st.party).id, "pozione_cura"),
              Action(A.CAST, attore.id, r.choice(st.party).id, "cura"),
              Action(A.EQUIP, attore.id, value="spada_lunga")]
    if uscite:
        direzione = r.choice(uscite)
        # Il movimento pesa di piu', altrimenti la partita non avanza mai.
        scelte += [Action(A.MOVE, capo, value=direzione)] * 6
        scelte.append(Action(A.SCOUT, attore.id, value=direzione))
    return r.choice(scelte)


@pytest.mark.parametrize("seed", [f"FUZZ-{i}" for i in range(25)])
def test_partita_casuale_mantiene_le_invarianti(seed):
    r = Roller(f"scelte-{seed}")
    st = new_game("g", seed)
    for i in range(4):
        st, _ = reduce(st, Action(A.JOIN, f"pg{i+1}", f"Eroe{i+1}", str(CLASSI[i % 4])))
    st, _ = reduce(st, Action(A.BEGIN))
    _verifica_invarianti(st)

    precedente = st
    for _ in range(400):
        if st.over:
            break
        st, eventi = reduce(st, _azione_casuale(st, r))
        _verifica_invarianti(st)
        assert st.roll_count >= precedente.roll_count, "il contatore dei dadi e' tornato indietro"
        assert eventi, "ogni azione deve produrre almeno un evento"
        precedente = st


@pytest.mark.parametrize("seed", [f"SOLO-{i}" for i in range(10)])
def test_partita_in_solitaria(seed):
    """Un solo giocatore: il caso limite dove il capo e' anche l'unico bersaglio."""
    r = Roller(f"scelte-{seed}")
    st = new_game("g", seed)
    st, _ = reduce(st, Action(A.JOIN, "pg1", "Solo", str(ClassId.LADRO)))
    st, _ = reduce(st, Action(A.BEGIN))
    for _ in range(200):
        if st.over:
            break
        st, _ = reduce(st, _azione_casuale(st, r))
        _verifica_invarianti(st)


def test_una_partita_arriva_alla_fine():
    """Con un po' di semi, almeno una run deve concludersi davvero."""
    esiti = set()
    for n in range(30):
        r = Roller(f"finale-{n}")
        st = new_game("g", f"FINALE-{n}")
        for i in range(4):
            st, _ = reduce(st, Action(A.JOIN, f"pg{i+1}", f"Eroe{i+1}", str(CLASSI[i % 4])))
        st, _ = reduce(st, Action(A.BEGIN))
        for _ in range(600):
            if st.over:
                break
            st, _ = reduce(st, _azione_casuale(st, r))
        if st.over:
            esiti.add(st.phase)
    assert esiti, "nessuna partita si e' mai conclusa"
