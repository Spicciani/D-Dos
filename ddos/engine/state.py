"""Lo stato completo di una partita, serializzabile in JSON.

Tutto quello che serve per riprendere una run sta qui dentro, `roll_count`
compreso: ricaricare uno stato e riapplicare le stesse azioni produce gli stessi
tiri di dado.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ddos.engine.dice import Roller
from ddos.engine.dungeon import Level
from ddos.engine.entities import Character, Combatant, Monster

MAX_TIER = 3
PARTY_MAX = 6


class Phase(StrEnum):
    LOBBY = "lobby"
    ESPLORAZIONE = "esplorazione"
    COMBATTIMENTO = "combattimento"
    VITTORIA = "vittoria"
    SCONFITTA = "sconfitta"


@dataclass(slots=True)
class Combat:
    """Il combattimento in corso: mostri, ordine di iniziativa, turno corrente."""

    monsters: list[Monster] = field(default_factory=list)
    order: list[str] = field(default_factory=list)   # id in ordine di iniziativa
    index: int = 0
    round: int = 1
    initiative: dict[str, int] = field(default_factory=dict)
    xp_pool: int = 0

    @property
    def current_id(self) -> str:
        return self.order[self.index] if self.order else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "monsters": [m.to_dict() for m in self.monsters],
            "order": list(self.order), "index": self.index, "round": self.round,
            "initiative": dict(self.initiative), "xp_pool": self.xp_pool,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Combat":
        return cls(
            monsters=[Monster.from_dict(m) for m in data.get("monsters", [])],
            order=list(data.get("order", [])), index=data.get("index", 0),
            round=data.get("round", 1), initiative=dict(data.get("initiative", {})),
            xp_pool=data.get("xp_pool", 0),
        )


@dataclass(slots=True)
class GameState:
    id: str
    seed: str
    roll_count: int = 0
    phase: Phase = Phase.LOBBY
    party: list[Character] = field(default_factory=list)
    leader_id: str = ""
    level: Level | None = None
    tier: int = 1
    room_id: str = ""
    prev_room_id: str = ""
    combat: Combat | None = None
    turn_seq: int = 0
    gold: int = 0
    pending_loot: list[str] = field(default_factory=list)
    fled_from: str = ""      # stanza da cui si e' appena fuggiti
    turn_mode: str = "live"  # live | lento
    finished_at: str = ""

    # --- accessi comodi ----------------------------------------------------
    def roller(self) -> Roller:
        """Roller posizionato sul contatore corrente. Ricordarsi di `sync`."""
        return Roller(self.seed, self.roll_count)

    def sync(self, roller: Roller) -> None:
        self.roll_count = roller.counter

    def char(self, char_id: str) -> Character | None:
        return next((c for c in self.party if c.id == char_id), None)

    def char_by_telegram(self, telegram_id: int) -> Character | None:
        return next((c for c in self.party if c.telegram_id == telegram_id), None)

    def monster(self, monster_id: str) -> Monster | None:
        if not self.combat:
            return None
        return next((m for m in self.combat.monsters if m.id == monster_id), None)

    def combatant(self, entity_id: str) -> Combatant | None:
        return self.char(entity_id) or self.monster(entity_id)

    @property
    def alive_party(self) -> list[Character]:
        return [c for c in self.party if c.alive]

    @property
    def standing_party(self) -> list[Character]:
        """Chi puo' ancora agire: esclude morenti e morti."""
        return [c for c in self.party if c.alive]

    @property
    def living_party(self) -> list[Character]:
        """Chi non e' morto: include i morenti, che si possono ancora curare."""
        return [c for c in self.party if not c.dead]

    @property
    def live_monsters(self) -> list[Monster]:
        return [m for m in self.combat.monsters if m.alive] if self.combat else []

    @property
    def room(self):
        if self.level is None or not self.room_id:
            return None
        return self.level.rooms.get(self.room_id)

    @property
    def leader(self) -> Character | None:
        return self.char(self.leader_id)

    @property
    def over(self) -> bool:
        return self.phase in (Phase.VITTORIA, Phase.SCONFITTA)

    # --- serializzazione ---------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "seed": self.seed, "roll_count": self.roll_count,
            "phase": str(self.phase), "party": [c.to_dict() for c in self.party],
            "leader_id": self.leader_id,
            "level": self.level.to_dict() if self.level else None,
            "tier": self.tier, "room_id": self.room_id,
            "prev_room_id": self.prev_room_id,
            "combat": self.combat.to_dict() if self.combat else None,
            "turn_seq": self.turn_seq, "gold": self.gold,
            "pending_loot": list(self.pending_loot), "fled_from": self.fled_from,
            "turn_mode": self.turn_mode, "finished_at": self.finished_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GameState":
        return cls(
            id=data["id"], seed=data["seed"], roll_count=data.get("roll_count", 0),
            phase=Phase(data.get("phase", Phase.LOBBY)),
            party=[Character.from_dict(c) for c in data.get("party", [])],
            leader_id=data.get("leader_id", ""),
            level=Level.from_dict(data["level"]) if data.get("level") else None,
            tier=data.get("tier", 1), room_id=data.get("room_id", ""),
            prev_room_id=data.get("prev_room_id", ""),
            combat=Combat.from_dict(data["combat"]) if data.get("combat") else None,
            turn_seq=data.get("turn_seq", 0), gold=data.get("gold", 0),
            pending_loot=list(data.get("pending_loot", [])),
            fled_from=data.get("fled_from", ""),
            turn_mode=data.get("turn_mode", "live"),
            finished_at=data.get("finished_at", ""),
        )


def new_game(game_id: str, seed: str, turn_mode: str = "live") -> GameState:
    return GameState(id=game_id, seed=seed, turn_mode=turn_mode)
