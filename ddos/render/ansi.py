"""Primitive di disegno in stile DOS.

Vincolo duro: **32 colonne**. Telegram rende i blocchi `<pre>` in monospace, ma
su un telefono stretto tutto cio' che supera ~32 caratteri va a capo e sfonda la
cornice. Ogni funzione qui dentro rispetta `WIDTH`.

I colori ANSI servono solo al client da terminale: su Telegram non esistono, e
infatti sono disattivati per default.
"""

from __future__ import annotations

from dataclasses import dataclass

WIDTH = 32
INNER = WIDTH - 4  # due caratteri di cornice piu' uno spazio per lato

# Cornici CP437, quelle dei menu dei BBS.
TL, TR, BL, BR = "╔", "╗", "╚", "╝"
HZ, VT = "═", "║"
FULL, EMPTY = "█", "░"


@dataclass(frozen=True, slots=True)
class Palette:
    """Codici ANSI, o stringhe vuote quando il colore non serve."""

    enabled: bool = False

    def _c(self, code: str) -> str:
        return code if self.enabled else ""

    @property
    def reset(self) -> str:
        return self._c("\033[0m")

    @property
    def dim(self) -> str:
        return self._c("\033[2m")

    @property
    def bold(self) -> str:
        return self._c("\033[1m")

    @property
    def green(self) -> str:
        return self._c("\033[32m")

    @property
    def bright(self) -> str:
        return self._c("\033[92m")

    @property
    def red(self) -> str:
        return self._c("\033[31m")

    @property
    def yellow(self) -> str:
        return self._c("\033[33m")

    @property
    def cyan(self) -> str:
        return self._c("\033[36m")

    @property
    def magenta(self) -> str:
        return self._c("\033[35m")


PLAIN = Palette(enabled=False)
COLOR = Palette(enabled=True)


def clip(text: str, width: int = INNER) -> str:
    """Taglia a `width`, con l'ellissi a un carattere solo per non sprecare spazio."""
    text = text.replace("\n", " ").replace("\t", " ")
    return text if len(text) <= width else text[: width - 1] + "~"


def wrap(text: str, width: int = INNER) -> list[str]:
    """A capo sulle parole. Le parole piu' lunghe della riga vengono spezzate."""
    righe: list[str] = []
    corrente = ""
    for parola in text.split():
        while len(parola) > width:
            if corrente:
                righe.append(corrente)
                corrente = ""
            righe.append(parola[:width])
            parola = parola[width:]
        if not corrente:
            corrente = parola
        elif len(corrente) + 1 + len(parola) <= width:
            corrente += " " + parola
        else:
            righe.append(corrente)
            corrente = parola
    if corrente:
        righe.append(corrente)
    return righe or [""]


def rule(width: int = WIDTH) -> str:
    return HZ * width


def box(lines: list[str], title: str = "", width: int = WIDTH) -> list[str]:
    """Riquadro a doppia linea con titolo incassato nel bordo superiore."""
    inner = width - 4
    if title:
        etichetta = f" {clip(title, inner - 2)} "
        riempimento = width - 2 - len(etichetta)
        sinistra = 1
        testa = TL + HZ * sinistra + etichetta + HZ * max(0, riempimento - sinistra) + TR
    else:
        testa = TL + HZ * (width - 2) + TR

    corpo = [f"{VT} {clip(riga, inner):<{inner}} {VT}" for riga in lines]
    return [testa, *corpo, BL + HZ * (width - 2) + BR]


def bar(current: int, maximum: int, width: int = 10) -> str:
    """Barra PF `[████▒▒▒▒▒▒]`. Sopra 0 PF mostra sempre almeno un blocco."""
    if maximum <= 0:
        return "[" + EMPTY * width + "]"
    ratio = max(0.0, min(1.0, current / maximum))
    pieni = int(ratio * width)
    if current > 0 and pieni == 0:
        pieni = 1
    if current < maximum and pieni == width:
        pieni = width - 1
    return "[" + FULL * pieni + EMPTY * (width - pieni) + "]"


def kv(label: str, value: str, width: int = INNER) -> str:
    """Etichetta a sinistra, valore a destra, puntini in mezzo."""
    value = str(value)
    spazio = width - len(value) - 1
    label = clip(label, max(1, spazio))
    punti = "." * max(1, width - len(label) - len(value))
    return f"{label}{punti}{value}"


def columns(items: list[str], per_row: int = 2, width: int = INNER) -> list[str]:
    """Dispone voci brevi su piu' colonne senza mai sforare."""
    if not items:
        return []
    cella = width // per_row
    righe = []
    for i in range(0, len(items), per_row):
        gruppo = items[i : i + per_row]
        righe.append("".join(clip(v, cella - 1).ljust(cella) for v in gruppo).rstrip())
    return righe


TITLE_ART = r"""
 ___     ___   ___  ___
|   \ __|   \ / _ \/ __|
| |) |__| |) | (_) \__ \
|___/   |___/ \___/|___/
""".strip("\n")


def title_screen(subtitle: str = "", width: int = WIDTH) -> list[str]:
    """Schermata titolo senza cornice: e' quello che facevano i giochi veri."""
    righe = [riga.center(width).rstrip() for riga in TITLE_ART.split("\n")]
    righe.append("dungeon crawl per compagnie".center(width).rstrip())
    if subtitle:
        righe.append(clip(subtitle, width).center(width).rstrip())
    return righe
