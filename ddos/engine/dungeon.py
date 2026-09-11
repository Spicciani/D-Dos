"""Generazione del dungeon: un grafo di stanze su griglia, tutto dal seed.

Garanzie del generatore, verificate dai test:
  * ogni stanza e' raggiungibile dall'ingresso;
  * l'uscita e' la stanza piu' lontana dall'ingresso, cosi' il livello si
    attraversa davvero invece di finire al primo passo;
  * l'ingresso e' sempre sgombro (nessuno finisce in un'imboscata al secondo 0).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable

from ddos.engine import content as C
from ddos.engine.dice import Roller

GRID_W = 5
GRID_H = 5

#: direzione -> (dx, dy)
DIRECTIONS: dict[str, tuple[int, int]] = {"n": (0, -1), "s": (0, 1), "e": (1, 0), "o": (-1, 0)}
DIRECTION_NAMES = {"n": "Nord", "s": "Sud", "e": "Est", "o": "Ovest"}
OPPOSITE = {"n": "s", "s": "n", "e": "o", "o": "e"}


class RoomKind(StrEnum):
    INGRESSO = "ingresso"
    VUOTA = "vuota"
    MOSTRI = "mostri"
    TESORO = "tesoro"
    TRAPPOLA = "trappola"
    SANTUARIO = "santuario"
    SCALA = "scala"
    BOSS = "boss"


#: Glifo sulla mappa per le stanze gia' visitate.
ROOM_GLYPH: dict[str, str] = {
    RoomKind.INGRESSO: "<",
    RoomKind.VUOTA: ".",
    RoomKind.MOSTRI: "!",
    RoomKind.TESORO: "$",
    RoomKind.TRAPPOLA: "^",
    RoomKind.SANTUARIO: "+",
    RoomKind.SCALA: ">",
    RoomKind.BOSS: "&",
}

ROOM_LABEL: dict[str, str] = {
    RoomKind.INGRESSO: "Ingresso",
    RoomKind.VUOTA: "Stanza vuota",
    RoomKind.MOSTRI: "Nemici!",
    RoomKind.TESORO: "Tesoro",
    RoomKind.TRAPPOLA: "Trappola",
    RoomKind.SANTUARIO: "Santuario",
    RoomKind.SCALA: "Scala verso il basso",
    RoomKind.BOSS: "Tana del boss",
}

#: Descrizioni d'atmosfera, scelte dal seed. Una per tipo di stanza.
FLAVOR: dict[str, tuple[str, ...]] = {
    RoomKind.INGRESSO: (
        "Aria fredda sale dalle scale alle vostre spalle.",
        "Il portale si chiude. Da qui si va solo avanti.",
    ),
    RoomKind.VUOTA: (
        "Polvere, silenzio, e un gocciolio da qualche parte.",
        "Ossa vecchie lungo le pareti. Nessuna e' recente.",
        "Il vostro respiro e' l'unico rumore.",
        "Muri incisi da graffi troppo alti per essere umani.",
    ),
    RoomKind.MOSTRI: (
        "Qualcosa si muove nel buio, e vi ha gia' visti.",
        "Occhi gialli si accendono a mezz'aria.",
        "Un ringhio basso. Poi un altro. Poi molti.",
    ),
    RoomKind.TESORO: (
        "Un baule sfondato, ma non svuotato.",
        "Un luccichio sotto un mucchio di stracci.",
        "Qualcuno ha nascosto qui la sua ultima fortuna.",
    ),
    RoomKind.TRAPPOLA: (
        "Il pavimento suona vuoto sotto i vostri stivali.",
        "Un filo teso all'altezza delle caviglie.",
        "Le lastre del pavimento non combaciano.",
    ),
    RoomKind.SANTUARIO: (
        "Un altare consumato. La fiamma sopra non si e' mai spenta.",
        "Una statua senza volto veglia su una vasca d'acqua limpida.",
    ),
    RoomKind.SCALA: (
        "Una scala a chiocciola sprofonda nel buio.",
        "Gradini bagnati scendono oltre la luce delle torce.",
    ),
    RoomKind.BOSS: (
        "La sala si apre enorme. In fondo, qualcosa vi aspettava.",
        "Il soffitto scompare nell'ombra. E l'ombra respira.",
    ),
}


@dataclass(slots=True)
class Room:
    id: str
    x: int
    y: int
    kind: str
    exits: dict[str, str] = field(default_factory=dict)  # direzione -> id stanza
    visited: bool = False
    cleared: bool = False
    flavor: str = ""
    monster_kind: str = ""
    monster_count: int = 0
    loot: list[str] = field(default_factory=list)
    gold: int = 0
    trap: str = ""
    trap_found: bool = False

    @property
    def glyph(self) -> str:
        return ROOM_GLYPH.get(self.kind, "?")

    @property
    def label(self) -> str:
        return ROOM_LABEL.get(self.kind, "?")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "x": self.x, "y": self.y, "kind": str(self.kind),
            "exits": dict(self.exits), "visited": self.visited, "cleared": self.cleared,
            "flavor": self.flavor, "monster_kind": self.monster_kind,
            "monster_count": self.monster_count, "loot": list(self.loot),
            "gold": self.gold, "trap": self.trap, "trap_found": self.trap_found,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Room":
        return cls(
            id=data["id"], x=data["x"], y=data["y"], kind=data["kind"],
            exits=dict(data.get("exits", {})), visited=data.get("visited", False),
            cleared=data.get("cleared", False), flavor=data.get("flavor", ""),
            monster_kind=data.get("monster_kind", ""),
            monster_count=data.get("monster_count", 0), loot=list(data.get("loot", [])),
            gold=data.get("gold", 0), trap=data.get("trap", ""),
            trap_found=data.get("trap_found", False),
        )


@dataclass(slots=True)
class Level:
    tier: int
    rooms: dict[str, Room]
    entry_id: str
    exit_id: str
    width: int = GRID_W
    height: int = GRID_H

    def room(self, room_id: str) -> Room:
        return self.rooms[room_id]

    def neighbors(self, room_id: str) -> dict[str, Room]:
        return {d: self.rooms[rid] for d, rid in self.rooms[room_id].exits.items()}

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "rooms": {rid: r.to_dict() for rid, r in self.rooms.items()},
            "entry_id": self.entry_id, "exit_id": self.exit_id,
            "width": self.width, "height": self.height,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Level":
        return cls(
            tier=data["tier"],
            rooms={rid: Room.from_dict(r) for rid, r in data["rooms"].items()},
            entry_id=data["entry_id"], exit_id=data["exit_id"],
            width=data.get("width", GRID_W), height=data.get("height", GRID_H),
        )


def _room_id(tier: int, x: int, y: int) -> str:
    return f"L{tier}-{x}{y}"


def _carve(roller: Roller, tier: int, target_rooms: int) -> dict[str, Room]:
    """Albero di copertura casuale: cresce dall'ingresso, resta sempre connesso."""
    start = (roller.randbelow(GRID_W), GRID_H - 1)  # si entra dal basso
    rooms: dict[tuple[int, int], Room] = {
        start: Room(id=_room_id(tier, *start), x=start[0], y=start[1], kind=RoomKind.INGRESSO)
    }
    frontier = [start]

    while len(rooms) < target_rooms and frontier:
        # Prendere dal fondo con una spinta casuale produce corridoi sinuosi
        # invece del blob compatto che darebbe una scelta puramente casuale.
        idx = len(frontier) - 1 - roller.randbelow(min(3, len(frontier)))
        cx, cy = frontier[idx]
        options = []
        for direction, (dx, dy) in DIRECTIONS.items():
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < GRID_W and 0 <= ny < GRID_H and (nx, ny) not in rooms:
                options.append((direction, nx, ny))
        if not options:
            frontier.pop(idx)
            continue
        direction, nx, ny = roller.choice(options)
        new_room = Room(id=_room_id(tier, nx, ny), x=nx, y=ny, kind=RoomKind.VUOTA)
        rooms[(nx, ny)] = new_room
        rooms[(cx, cy)].exits[direction] = new_room.id
        new_room.exits[OPPOSITE[direction]] = rooms[(cx, cy)].id
        frontier.append((nx, ny))

    return {r.id: r for r in rooms.values()}


def _distances(rooms: dict[str, Room], start: str) -> dict[str, int]:
    dist = {start: 0}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for neighbor in rooms[current].exits.values():
            if neighbor not in dist:
                dist[neighbor] = dist[current] + 1
                queue.append(neighbor)
    return dist


def _weighted_choice(roller: Roller, table: Iterable[tuple[str, int]]) -> str:
    entries = list(table)
    total = sum(weight for _, weight in entries)
    pick = roller.randbelow(total)
    for key, weight in entries:
        pick -= weight
        if pick < 0:
            return key
    return entries[-1][0]


def _populate(roller: Roller, level: Level, boss_level: bool) -> None:
    """Assegna tipo e contenuto a ogni stanza che non sia ingresso o uscita."""
    tier = level.tier
    monster_pool = [m.kind for m in C.monsters_for_tier(tier)]
    trap_pool = [t.key for t in C.traps_for_tier(tier)]
    loot_table = C.LOOT_TABLES.get(tier, C.LOOT_TABLES[1])

    weights: tuple[tuple[str, int], ...] = (
        (RoomKind.MOSTRI, 42),
        (RoomKind.TESORO, 18),
        (RoomKind.TRAPPOLA, 16),
        (RoomKind.VUOTA, 18),
        (RoomKind.SANTUARIO, 6),
    )

    for room in level.rooms.values():
        if room.id == level.entry_id:
            room.kind = RoomKind.INGRESSO
            room.cleared = True
        elif room.id == level.exit_id:
            room.kind = RoomKind.BOSS if boss_level else RoomKind.SCALA
        else:
            room.kind = _weighted_choice(roller, weights)

        if room.kind == RoomKind.MOSTRI:
            room.monster_kind = roller.choice(monster_pool)
            low, high = C.BESTIARY[room.monster_kind].group
            room.monster_count = roller.randint(low, high)
        elif room.kind == RoomKind.TESORO:
            room.loot = [_weighted_choice(roller, loot_table)]
            if roller.chance(0.35):
                room.loot.append(_weighted_choice(roller, loot_table))
            room.gold = roller.roll_total(f"{tier}d20+{tier * 5}")
        elif room.kind == RoomKind.TRAPPOLA:
            room.trap = roller.choice(trap_pool)
        elif room.kind == RoomKind.BOSS:
            room.monster_kind = C.boss_for_tier(tier).kind
            room.monster_count = 1
            room.loot = [_weighted_choice(roller, loot_table)]
            room.gold = roller.roll_total(f"{tier}d20+{tier * 20}")

        room.flavor = roller.choice(FLAVOR.get(room.kind, ("...",)))


def generate_level(roller: Roller, tier: int, *, boss_level: bool = False) -> Level:
    """Un livello completo e coerente. Stesso roller, stesso livello."""
    target = 8 + roller.randbelow(4) + tier  # 9-14 stanze
    rooms = _carve(roller, tier, min(target, GRID_W * GRID_H))
    entry_id = next(r.id for r in rooms.values() if r.kind == RoomKind.INGRESSO)

    distances = _distances(rooms, entry_id)
    # La stanza piu' lontana diventa l'uscita; a parita' vince l'id, per determinismo.
    exit_id = max(sorted(distances), key=lambda rid: distances[rid])

    level = Level(tier=tier, rooms=rooms, entry_id=entry_id, exit_id=exit_id)
    _populate(roller, level, boss_level)
    return level


def reachable_from(level: Level, start: str | None = None) -> set[str]:
    """Utilita' per i test e per il debug del generatore."""
    return set(_distances(level.rooms, start or level.entry_id))
