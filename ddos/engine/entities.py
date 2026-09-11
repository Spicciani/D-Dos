"""Entita' di gioco: personaggi, mostri, oggetti.

Solo dati e logica che non richiede i cataloghi (quelli stanno in `content.py`).
Tutto e' serializzabile in JSON: lo stato di una partita e' un blob salvabile.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Union


class Ability(StrEnum):
    FOR = "FOR"  # Forza
    DES = "DES"  # Destrezza
    COS = "COS"  # Costituzione
    INT = "INT"  # Intelligenza
    SAG = "SAG"  # Saggezza
    CAR = "CAR"  # Carisma


ABILITY_ORDER: tuple[Ability, ...] = (
    Ability.FOR,
    Ability.DES,
    Ability.COS,
    Ability.INT,
    Ability.SAG,
    Ability.CAR,
)


class ClassId(StrEnum):
    GUERRIERO = "guerriero"
    LADRO = "ladro"
    MAGO = "mago"
    CHIERICO = "chierico"


class Status(StrEnum):
    VIVO = "vivo"
    MORENTE = "morente"
    MORTO = "morto"


class Condition(StrEnum):
    """Stati temporanei. Durano un numero di round."""

    DIFESA = "difesa"          # +2 CA fino al proprio turno successivo
    SCUDO = "scudo"            # +4 CA, Scudo Arcano
    BENEDETTO = "benedetto"    # +1 ai tiri per colpire e alle salvezze
    VELENO = "veleno"          # danno a inizio turno
    STORDITO = "stordito"      # salta il turno
    NASCOSTO = "nascosto"      # il prossimo attacco e' furtivo
    PROVOCATO = "provocato"    # il mostro attacca chi lo ha provocato


def ability_mod(score: int) -> int:
    """Modificatore d20 classico: 10-11 -> 0, ogni 2 punti -> +/-1."""
    return (score - 10) // 2


@dataclass(slots=True)
class Item:
    """Definizione di un oggetto nel catalogo."""

    key: str
    name: str
    kind: str  # arma | armatura | pozione | tesoro | chiave | pergamena
    damage: str = ""          # armi: espressione di dadi
    ac_bonus: int = 0         # armature
    heal: str = ""            # pozioni
    value: int = 0            # monete d'oro
    two_handed: bool = False
    classes: tuple[ClassId, ...] = ()  # se vuoto: usabile da tutti
    description: str = ""

    def usable_by(self, cls: ClassId) -> bool:
        return not self.classes or cls in self.classes


@dataclass(slots=True)
class Character:
    """Un personaggio giocante."""

    id: str
    name: str
    cls: ClassId
    abilities: dict[Ability, int]
    max_hp: int
    hp: int
    level: int = 1
    xp: int = 0
    status: Status = Status.VIVO
    death_successes: int = 0
    death_failures: int = 0
    weapon: str = ""
    armor: str = ""
    inventory: dict[str, int] = field(default_factory=dict)
    spell_slots: int = 0
    spell_slots_max: int = 0
    conditions: dict[str, int] = field(default_factory=dict)
    gold: int = 0
    telegram_id: int | None = None
    kills: int = 0

    # --- derivati semplici -------------------------------------------------
    @property
    def alive(self) -> bool:
        return self.status is Status.VIVO

    @property
    def down(self) -> bool:
        """Fuori combattimento: morente o morto."""
        return self.status is not Status.VIVO

    @property
    def dead(self) -> bool:
        return self.status is Status.MORTO

    def mod(self, ability: Ability) -> int:
        return ability_mod(self.abilities[ability])

    def has_condition(self, condition: Condition | str) -> bool:
        return str(condition) in self.conditions

    def add_condition(self, condition: Condition | str, rounds: int) -> None:
        key = str(condition)
        self.conditions[key] = max(self.conditions.get(key, 0), rounds)

    def clear_condition(self, condition: Condition | str) -> None:
        self.conditions.pop(str(condition), None)

    def add_item(self, key: str, qty: int = 1) -> None:
        if qty <= 0:
            return
        self.inventory[key] = self.inventory.get(key, 0) + qty

    def remove_item(self, key: str, qty: int = 1) -> bool:
        """Toglie `qty` copie. False se non ce ne sono abbastanza."""
        if qty <= 0:
            return True  # togliere zero copie riesce sempre, anche di nulla
        have = self.inventory.get(key, 0)
        if have < qty:
            return False
        if have == qty:
            del self.inventory[key]
        else:
            self.inventory[key] = have - qty
        return True

    # --- serializzazione ---------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "cls": str(self.cls),
            "abilities": {str(k): v for k, v in self.abilities.items()},
            "max_hp": self.max_hp,
            "hp": self.hp,
            "level": self.level,
            "xp": self.xp,
            "status": str(self.status),
            "death_successes": self.death_successes,
            "death_failures": self.death_failures,
            "weapon": self.weapon,
            "armor": self.armor,
            "inventory": dict(self.inventory),
            "spell_slots": self.spell_slots,
            "spell_slots_max": self.spell_slots_max,
            "conditions": dict(self.conditions),
            "gold": self.gold,
            "telegram_id": self.telegram_id,
            "kills": self.kills,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Character":
        return cls(
            id=data["id"],
            name=data["name"],
            cls=ClassId(data["cls"]),
            abilities={Ability(k): v for k, v in data["abilities"].items()},
            max_hp=data["max_hp"],
            hp=data["hp"],
            level=data.get("level", 1),
            xp=data.get("xp", 0),
            status=Status(data.get("status", Status.VIVO)),
            death_successes=data.get("death_successes", 0),
            death_failures=data.get("death_failures", 0),
            weapon=data.get("weapon", ""),
            armor=data.get("armor", ""),
            inventory=dict(data.get("inventory", {})),
            spell_slots=data.get("spell_slots", 0),
            spell_slots_max=data.get("spell_slots_max", 0),
            conditions=dict(data.get("conditions", {})),
            gold=data.get("gold", 0),
            telegram_id=data.get("telegram_id"),
            kills=data.get("kills", 0),
        )


@dataclass(slots=True)
class Monster:
    """Un mostro istanziato in combattimento.

    `kind` punta al bestiario, `id` distingue le copie nella stessa stanza
    (`goblin-1`, `goblin-2`), `label` e' la lettera mostrata al giocatore.
    """

    id: str
    kind: str
    name: str
    max_hp: int
    hp: int
    ac: int
    attack_bonus: int
    damage: str
    xp: int
    initiative_mod: int = 0
    label: str = "A"
    traits: tuple[str, ...] = ()
    conditions: dict[str, int] = field(default_factory=dict)

    @property
    def alive(self) -> bool:
        return self.hp > 0

    @property
    def down(self) -> bool:
        return self.hp <= 0

    @property
    def dead(self) -> bool:
        return self.hp <= 0

    @property
    def display(self) -> str:
        return f"{self.name} {self.label}"

    def has_condition(self, condition: Condition | str) -> bool:
        return str(condition) in self.conditions

    def add_condition(self, condition: Condition | str, rounds: int) -> None:
        key = str(condition)
        self.conditions[key] = max(self.conditions.get(key, 0), rounds)

    def clear_condition(self, condition: Condition | str) -> None:
        self.conditions.pop(str(condition), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "max_hp": self.max_hp,
            "hp": self.hp,
            "ac": self.ac,
            "attack_bonus": self.attack_bonus,
            "damage": self.damage,
            "xp": self.xp,
            "initiative_mod": self.initiative_mod,
            "label": self.label,
            "traits": list(self.traits),
            "conditions": dict(self.conditions),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Monster":
        return cls(
            id=data["id"],
            kind=data["kind"],
            name=data["name"],
            max_hp=data["max_hp"],
            hp=data["hp"],
            ac=data["ac"],
            attack_bonus=data["attack_bonus"],
            damage=data["damage"],
            xp=data["xp"],
            initiative_mod=data.get("initiative_mod", 0),
            label=data.get("label", "A"),
            traits=tuple(data.get("traits", ())),
            conditions=dict(data.get("conditions", {})),
        )


Combatant = Union[Character, Monster]
