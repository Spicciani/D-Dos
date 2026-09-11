"""Dadi deterministici.

Il gioco non usa mai `random` globale: ogni tiro deriva da `(seed, counter)`
tramite un hash, quindi la N-esima estrazione di una partita e' sempre la stessa
a parita' di seed. Il contatore vive dentro lo stato della partita, cosi' una run
si puo' salvare, ricaricare e rigiocare evento per evento.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Sequence, TypeVar

T = TypeVar("T")

_DICE_RE = re.compile(r"^\s*(\d*)\s*d\s*(\d+)\s*(?:([+-])\s*(\d+))?\s*$", re.IGNORECASE)
_MASK = (1 << 64) - 1


@dataclass(frozen=True, slots=True)
class DiceSpec:
    """Una espressione di dadi, es. `2d6+3`."""

    count: int
    sides: int
    bonus: int = 0

    def __str__(self) -> str:
        base = f"{self.count}d{self.sides}"
        if self.bonus > 0:
            return f"{base}+{self.bonus}"
        if self.bonus < 0:
            return f"{base}{self.bonus}"
        return base

    @property
    def average(self) -> float:
        return self.count * (self.sides + 1) / 2 + self.bonus

    @property
    def maximum(self) -> int:
        return self.count * self.sides + self.bonus


@dataclass(frozen=True, slots=True)
class RollResult:
    """Esito di un tiro: i singoli dadi restano visibili, come al tavolo."""

    dice: tuple[int, ...]
    bonus: int
    total: int

    def __str__(self) -> str:
        parts = "+".join(str(d) for d in self.dice)
        if self.bonus:
            return f"[{parts}]{self.bonus:+d} = {self.total}"
        return f"[{parts}] = {self.total}"


def parse_dice(expr: str | DiceSpec) -> DiceSpec:
    """Interpreta `"2d6+3"`, `"d20"`, `"3d6"`. Un DiceSpec passa attraverso."""
    if isinstance(expr, DiceSpec):
        return expr
    match = _DICE_RE.match(expr)
    if not match:
        raise ValueError(f"espressione di dadi non valida: {expr!r}")
    count_raw, sides_raw, sign, bonus_raw = match.groups()
    count = int(count_raw) if count_raw else 1
    sides = int(sides_raw)
    if count < 1:
        raise ValueError(f"numero di dadi non valido in {expr!r}")
    if sides < 2:
        raise ValueError(f"dado a {sides} facce non valido in {expr!r}")
    bonus = int(bonus_raw) if bonus_raw else 0
    if sign == "-":
        bonus = -bonus
    return DiceSpec(count=count, sides=sides, bonus=bonus)


class Roller:
    """Generatore riproducibile basato su contatore.

    Non tiene stato nascosto: `seed` e `counter` bastano a ricostruirlo. Dopo aver
    risolto un'azione si rilegge `counter` e lo si salva nello stato di gioco.
    """

    __slots__ = ("seed", "counter")

    def __init__(self, seed: str, counter: int = 0) -> None:
        self.seed = seed
        self.counter = counter

    def _next_raw(self) -> int:
        payload = f"{self.seed}#{self.counter}".encode("utf-8")
        self.counter += 1
        return int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big")

    def randbelow(self, n: int) -> int:
        """Intero uniforme in [0, n). Rifiuta i valori che introdurrebbero bias."""
        if n <= 0:
            raise ValueError("n deve essere positivo")
        limit = _MASK - (_MASK % n)
        while True:
            raw = self._next_raw()
            if raw <= limit:
                return raw % n

    def randint(self, low: int, high: int) -> int:
        """Intero uniforme in [low, high], estremi inclusi."""
        if high < low:
            raise ValueError("intervallo vuoto")
        return low + self.randbelow(high - low + 1)

    def d(self, sides: int) -> int:
        """Un singolo dado a `sides` facce."""
        return self.randint(1, sides)

    def d20(self) -> int:
        return self.d(20)

    def roll(self, expr: str | DiceSpec) -> RollResult:
        """Tira una espressione completa mantenendo i singoli dadi."""
        spec = parse_dice(expr)
        dice = tuple(self.d(spec.sides) for _ in range(spec.count))
        return RollResult(dice=dice, bonus=spec.bonus, total=max(0, sum(dice) + spec.bonus))

    def roll_total(self, expr: str | DiceSpec) -> int:
        return self.roll(expr).total

    def best_of(self, count: int, sides: int, keep: int) -> tuple[int, tuple[int, ...]]:
        """Tira `count` dadi e tiene i `keep` migliori (4d6 scarta il minore)."""
        dice = tuple(self.d(sides) for _ in range(count))
        kept = sorted(dice, reverse=True)[:keep]
        return sum(kept), dice

    def choice(self, seq: Sequence[T]) -> T:
        if not seq:
            raise ValueError("sequenza vuota")
        return seq[self.randbelow(len(seq))]

    def chance(self, probability: float) -> bool:
        """True con la probabilita' data (0.0-1.0)."""
        return self.randbelow(10_000) < int(probability * 10_000)
