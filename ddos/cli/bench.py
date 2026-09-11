"""Banco di prova del bilanciamento.

Gioca partite intere senza nessuno alla tastiera e riporta quante finiscono
bene. Serve ogni volta che si tocca una regola, un mostro o la curva dei PX:
un numero misurato batte una sensazione.

    python -m ddos.cli.bench --partite 40
    python -m ddos.cli.bench --corsa          # party che salta le stanze

Il bot non e' bravo: attacca il nemico piu' debole, cura sotto meta' PF, non
fugge mai e non cambia equipaggiamento. Va letto come il pavimento della
difficolta', non come il comportamento di giocatori veri.
"""

from __future__ import annotations

import argparse
import statistics
from collections import Counter, deque

from ddos.engine import actions as A
from ddos.engine.actions import Action
from ddos.engine.dungeon import RoomKind
from ddos.engine.entities import ClassId
from ddos.engine.reduce import reduce
from ddos.engine.state import GameState, Phase, new_game

COMPAGNIA = [("Grommash", ClassId.GUERRIERO), ("Silfa", ClassId.LADRO),
             ("Zilla", ClassId.MAGO), ("Bard", ClassId.CHIERICO)]


def _rotta(state: GameState, corsa: bool) -> str | None:
    """Prima direzione di un percorso verso l'obiettivo. None se non ce n'e'."""
    livello, stanza = state.level, state.room
    obiettivi = [lambda r: r.id == livello.exit_id, lambda r: not r.visited]
    if not corsa:
        obiettivi.reverse()  # prima ripulisce, poi scende

    for obiettivo in obiettivi:
        visti, coda = {stanza.id}, deque([(stanza.id, None)])
        while coda:
            rid, prima = coda.popleft()
            corrente = livello.rooms[rid]
            if rid != stanza.id and obiettivo(corrente):
                return prima
            for direzione, vicino in sorted(corrente.exits.items()):
                if vicino not in visti:
                    visti.add(vicino)
                    coda.append((vicino, direzione if prima is None else prima))
    return sorted(stanza.exits)[0] if stanza.exits else None


def _mossa(state: GameState, corsa: bool) -> Action | None:
    if state.phase is Phase.COMBATTIMENTO:
        attore = state.char(state.combat.current_id)
        nemico = min(state.live_monsters, key=lambda m: m.hp)
        if attore.cls is ClassId.CHIERICO and attore.spell_slots > 0:
            feriti = [c for c in state.party if not c.dead and c.hp < c.max_hp // 2]
            if feriti:
                return Action(A.CAST, attore.id, feriti[0].id, "cura")
        if attore.cls is ClassId.MAGO and attore.spell_slots > 0:
            return Action(A.CAST, attore.id, nemico.id, "dardo")
        if attore.cls is ClassId.GUERRIERO and attore.level >= 2:
            return Action(A.POWER, attore.id, nemico.id)
        return Action(A.ATTACK, attore.id, nemico.id)

    capo = state.leader if state.leader and state.leader.alive else None
    if capo is None:
        superstiti = state.standing_party
        if not superstiti:
            return None
        return Action(A.SET_LEADER, superstiti[0].id, superstiti[0].id)

    stanza = state.room
    if stanza.loot or stanza.gold:
        return Action(A.TAKE, capo.id)
    if stanza.kind == RoomKind.SANTUARIO and not stanza.cleared:
        return Action(A.PRAY, capo.id)
    if any(c.hp < c.max_hp // 3 for c in state.standing_party):
        return Action(A.REST, capo.id)
    if stanza.kind == RoomKind.SCALA and (
        corsa or all(r.visited for r in state.level.rooms.values())
    ):
        return Action(A.DESCEND, capo.id)
    direzione = _rotta(state, corsa)
    return Action(A.MOVE, capo.id, value=direzione) if direzione else None


def partita(seed: str, corsa: bool = False, limite: int = 1200) -> dict:
    state = new_game("bench", seed)
    for indice, (nome, cls) in enumerate(COMPAGNIA, start=1):
        state, _ = reduce(state, Action(A.JOIN, f"p{indice}", nome, str(cls)))
    state, _ = reduce(state, Action(A.BEGIN))

    passi = 0
    while not state.over and passi < limite:
        mossa = _mossa(state, corsa)
        if mossa is None:
            break
        nuovo, _ = reduce(state, mossa)
        if nuovo is state:      # azione rifiutata: evita il ciclo infinito
            break
        state, passi = nuovo, passi + 1

    return {
        "seed": seed,
        "esito": str(state.phase),
        "livello": state.tier,
        "passi": passi,
        "oro": state.gold,
        "morti": sum(1 for c in state.party if c.dead),
        "livello_max": max((c.level for c in state.party), default=1),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ddos-bench", description=__doc__)
    parser.add_argument("--partite", type=int, default=40)
    parser.add_argument("--corsa", action="store_true",
                        help="scendi appena trovi la scala, senza ripulire")
    parser.add_argument("--prefisso", default="BENCH")
    parser.add_argument("--dettaglio", action="store_true")
    args = parser.parse_args(argv)

    risultati = [partita(f"{args.prefisso}-{n}", args.corsa) for n in range(args.partite)]
    esiti = Counter(r["esito"] for r in risultati)
    vittorie = esiti.get("vittoria", 0)

    if args.dettaglio:
        for r in risultati:
            print(f"{r['seed']:14} {r['esito']:12} liv={r['livello']} "
                  f"px={r['livello_max']} morti={r['morti']} oro={r['oro']:4} "
                  f"passi={r['passi']}")
        print()

    print(f"partite: {len(risultati)}   modo: {'corsa' if args.corsa else 'ripulisci tutto'}")
    for esito, quante in esiti.most_common():
        print(f"  {esito:14} {quante:3}  ({quante / len(risultati):.0%})")
    print(f"vittorie: {vittorie}/{len(risultati)} ({vittorie / len(risultati):.0%})")
    print(f"livello raggiunto (mediana): {statistics.median(r['livello'] for r in risultati)}")
    print(f"livello personaggi (mediana): "
          f"{statistics.median(r['livello_max'] for r in risultati)}")
    print(f"caduti per partita (media): "
          f"{statistics.mean(r['morti'] for r in risultati):.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
