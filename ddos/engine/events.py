"""Eventi: i fatti accaduti, separati da come vengono raccontati.

Ogni funzione delle regole restituisce eventi invece di stampare. Il renderer li
traduce in schermate DOS, i test li asseriscono, e un eventuale narratore AI puo'
vestirli di prosa senza mai toccare i dadi.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Event:
    kind: str
    text: str
    data: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.text


def ev(kind: str, text: str, **data: Any) -> Event:
    return Event(kind=kind, text=text, data=data)


# Tipi di evento usati dal renderer per scegliere il colore/prefisso.
COLPO = "colpo"
CRITICO = "critico"
MANCATO = "mancato"
DANNO = "danno"
CURA = "cura"
MORTE = "morte"
MORENTE = "morente"
SALVEZZA = "salvezza"
INCANTESIMO = "incantesimo"
CONDIZIONE = "condizione"
BOTTINO = "bottino"
PX = "px"
LIVELLO = "livello"
TRAPPOLA = "trappola"
MOVIMENTO = "movimento"
INFO = "info"
SCENA = "scena"
ERRORE = "errore"
TURNO = "turno"
FINE = "fine"
