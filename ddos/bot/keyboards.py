"""Tastiere inline.

Due famiglie di callback:
  * `a:<turno>:<azione>` cambia lo stato. Porta sempre il numero di turno: se
    non coincide con quello corrente l'azione e' scaduta e viene ignorata, cosi'
    due tap simultanei non producono due colpi.
  * `m:<turno>:<voce>` apre solo un sottomenu. Non tocca lo stato, non consuma
    dadi, non ha bisogno del lock.

Il campo `attore` resta vuoto nei bottoni: chi agisce lo decide il server dal
telegram_id di chi ha premuto. Un bottone non puo' impersonare nessuno.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ddos.engine import actions as A
from ddos.engine import content as C
from ddos.engine.dungeon import DIRECTION_NAMES, RoomKind
from ddos.engine.entities import ClassId, Status
from ddos.engine.state import GameState, Phase

Button = InlineKeyboardButton
Markup = InlineKeyboardMarkup

DIR_ICON = {"n": "N", "s": "S", "e": "E", "o": "O"}


def act(seq: int, kind: str, target: str = "", value: str = "") -> str:
    return f"a:{seq}:{kind}||{target}|{value}"


def menu(seq: int, voce: str) -> str:
    return f"m:{seq}:{voce}"


def parse_callback(data: str) -> tuple[str, int, str]:
    """`("a"|"m", turno, resto)`. Solleva ValueError sui dati malformati."""
    tipo, _, coda = data.partition(":")
    if tipo not in ("a", "m"):
        raise ValueError(f"callback sconosciuta: {data!r}")
    seq_raw, _, resto = coda.partition(":")
    return tipo, int(seq_raw), resto


# --------------------------------------------------------------------------
# Lobby
# --------------------------------------------------------------------------


def class_kb(seq: int) -> Markup:
    righe = []
    voci = list(C.CLASSES.values())
    for i in range(0, len(voci), 2):
        righe.append([
            Button(text=f"{c.name}", callback_data=act(seq, A.JOIN, value=str(c.id)))
            for c in voci[i : i + 2]
        ])
    return Markup(inline_keyboard=righe)


def lobby_kb(state: GameState) -> Markup:
    seq = state.turn_seq
    righe = [[Button(text="Unisciti", callback_data=menu(seq, "classi"))]]
    if state.party:
        righe.append([Button(text="Si comincia", callback_data=act(seq, A.BEGIN))])
    return Markup(inline_keyboard=righe)


# --------------------------------------------------------------------------
# Esplorazione
# --------------------------------------------------------------------------


def _direzioni(state: GameState, kind: str) -> list[list[Button]]:
    stanza = state.room
    if stanza is None:
        return []
    presenti = [d for d in ("n", "s", "e", "o") if d in stanza.exits]
    if not presenti:
        return []
    return [[
        Button(text=DIR_ICON[d], callback_data=act(state.turn_seq, kind, value=d))
        for d in presenti
    ]]


def explore_kb(state: GameState, *, menu_aperto: str = "") -> Markup:
    seq = state.turn_seq
    stanza = state.room

    if menu_aperto == "spia":
        righe = _direzioni(state, A.SCOUT)
        righe.append([Button(text="< indietro", callback_data=menu(seq, "principale"))])
        return Markup(inline_keyboard=righe)

    righe = _direzioni(state, A.MOVE)

    contestuali: list[Button] = []
    if stanza is not None and not stanza.cleared:
        if stanza.loot or stanza.gold:
            contestuali.append(Button(text="Raccogli",
                                      callback_data=act(seq, A.TAKE)))
        if stanza.kind == RoomKind.SANTUARIO:
            contestuali.append(Button(text="Prega", callback_data=act(seq, A.PRAY)))
        if stanza.kind == RoomKind.TRAPPOLA and stanza.trap_found:
            contestuali.append(Button(text="Disinnesca",
                                      callback_data=act(seq, A.DISARM)))
    if stanza is not None and stanza.kind == RoomKind.SCALA:
        contestuali.append(Button(text="Scendi", callback_data=act(seq, A.DESCEND)))
    if contestuali:
        righe.append(contestuali)

    comuni = [
        Button(text="Cerca", callback_data=act(seq, A.SEARCH)),
        Button(text="Riposa", callback_data=act(seq, A.REST)),
    ]
    if any(c.cls is ClassId.LADRO and c.alive for c in state.party):
        comuni.append(Button(text="Spia", callback_data=menu(seq, "spia")))
    righe.append(comuni)

    ultima = [Button(text="Scheda", callback_data=menu(seq, "scheda"))]
    if any(c.spell_slots > 0 for c in state.standing_party):
        ultima.append(Button(text="Incantesimo", callback_data=menu(seq, "inc")))
    if any(c.inventory for c in state.standing_party):
        ultima.append(Button(text="Zaino", callback_data=menu(seq, "zaino")))
    righe.append(ultima)
    return Markup(inline_keyboard=righe)


# --------------------------------------------------------------------------
# Combattimento
# --------------------------------------------------------------------------


def _bersagli_mostri(state: GameState, kind: str, value: str = "") -> list[list[Button]]:
    righe, corrente = [], []
    for m in state.live_monsters:
        corrente.append(Button(
            text=f"{m.label} ({m.hp})",
            callback_data=act(state.turn_seq, kind, target=m.id, value=value),
        ))
        if len(corrente) == 3:
            righe.append(corrente)
            corrente = []
    if corrente:
        righe.append(corrente)
    return righe


def _bersagli_alleati(state: GameState, kind: str, value: str = "") -> list[list[Button]]:
    righe, corrente = [], []
    for c in state.party:
        if c.status is Status.MORTO:
            continue
        etichetta = c.name if c.alive else f"{c.name} (a terra)"
        corrente.append(Button(
            text=etichetta,
            callback_data=act(state.turn_seq, kind, target=c.id, value=value),
        ))
        if len(corrente) == 2:
            righe.append(corrente)
            corrente = []
    if corrente:
        righe.append(corrente)
    return righe


def combat_kb(state: GameState, *, menu_aperto: str = "") -> Markup:
    seq = state.turn_seq
    attore = state.char(state.combat.current_id) if state.combat else None
    indietro = [Button(text="< indietro", callback_data=menu(seq, "principale"))]

    if menu_aperto.startswith("inc:"):
        chiave = menu_aperto.split(":", 1)[1]
        spell = C.SPELLS.get(chiave)
        if spell is None:
            return combat_kb(state)
        if spell.target == C.TARGET_NEMICO:
            righe = _bersagli_mostri(state, A.CAST, chiave)
        elif spell.target == C.TARGET_ALLEATO:
            righe = _bersagli_alleati(state, A.CAST, chiave)
        else:
            righe = [[Button(text=f"Lancia {spell.name}",
                             callback_data=act(seq, A.CAST, value=chiave))]]
        return Markup(inline_keyboard=righe + [indietro])

    if menu_aperto == "inc" and attore is not None:
        righe = [
            [Button(text=f"{s.name} ({attore.spell_slots} slot)",
                    callback_data=menu(seq, f"inc:{s.key}"))]
            for s in C.spells_for(attore.cls, attore.level)
        ]
        return Markup(inline_keyboard=(righe or [[Button(
            text="Nessun incantesimo", callback_data=menu(seq, "principale"))]]) + [indietro])

    if menu_aperto.startswith("zaino:"):
        chiave = menu_aperto.split(":", 1)[1]
        return Markup(inline_keyboard=_bersagli_alleati(state, A.USE, chiave) + [indietro])

    if menu_aperto == "zaino" and attore is not None:
        righe = [
            [Button(text=f"{C.item(k).name} x{q}", callback_data=menu(seq, f"zaino:{k}"))]
            for k, q in sorted(attore.inventory.items())
            if C.item(k).kind == "pozione"
        ]
        return Markup(inline_keyboard=(righe or [[Button(
            text="Niente di utile", callback_data=menu(seq, "principale"))]]) + [indietro])

    if menu_aperto == "pod":
        return Markup(inline_keyboard=_bersagli_mostri(state, A.POWER) + [indietro])

    # --- menu principale del turno ---
    righe = _bersagli_mostri(state, A.ATTACK)
    speciali: list[Button] = []
    if attore is not None:
        if attore.cls is ClassId.GUERRIERO:
            speciali.append(Button(text="Poderoso", callback_data=menu(seq, "pod")))
            speciali.append(Button(text="Provoca", callback_data=act(seq, A.TAUNT)))
        if attore.cls is ClassId.LADRO:
            speciali.append(Button(text="Nasconditi", callback_data=act(seq, A.HIDE)))
        if attore.spell_slots > 0:
            speciali.append(Button(text="Incantesimo", callback_data=menu(seq, "inc")))
        if any(C.item(k).kind == "pozione" for k in attore.inventory):
            speciali.append(Button(text="Zaino", callback_data=menu(seq, "zaino")))
    if speciali:
        righe.append(speciali[:3])
        if speciali[3:]:
            righe.append(speciali[3:])

    righe.append([
        Button(text="Difenditi", callback_data=act(seq, A.DEFEND)),
        Button(text="Fuggi", callback_data=act(seq, A.FLEE)),
        Button(text="Scheda", callback_data=menu(seq, "scheda")),
    ])
    return Markup(inline_keyboard=righe)


# --------------------------------------------------------------------------
# Dispatcher
# --------------------------------------------------------------------------


def scene_kb(state: GameState, *, menu_aperto: str = "") -> Markup | None:
    if state.over:
        return None
    if state.phase is Phase.LOBBY:
        if menu_aperto == "classi":
            return class_kb(state.turn_seq)
        return lobby_kb(state)
    if state.phase is Phase.COMBATTIMENTO:
        return combat_kb(state, menu_aperto=menu_aperto)
    if menu_aperto.startswith("inc") or menu_aperto.startswith("zaino"):
        return _fuori_combattimento_kb(state, menu_aperto)
    return explore_kb(state, menu_aperto=menu_aperto)


def _fuori_combattimento_kb(state: GameState, menu_aperto: str) -> Markup:
    """Curarsi e bere pozioni tra uno scontro e l'altro: meta' del gioco."""
    seq = state.turn_seq
    indietro = [Button(text="< indietro", callback_data=menu(seq, "principale"))]

    if menu_aperto.startswith("inc:"):
        chiave = menu_aperto.split(":", 1)[1]
        return Markup(inline_keyboard=_bersagli_alleati(state, A.CAST, chiave) + [indietro])
    if menu_aperto.startswith("zaino:"):
        chiave = menu_aperto.split(":", 1)[1]
        return Markup(inline_keyboard=_bersagli_alleati(state, A.USE, chiave) + [indietro])

    if menu_aperto == "inc":
        visti: set[str] = set()
        righe = []
        for ch in state.standing_party:
            if ch.spell_slots <= 0:
                continue
            for s in C.spells_for(ch.cls, ch.level):
                if s.target in (C.TARGET_NEMICO, C.TARGET_TUTTI_NEMICI) or s.key in visti:
                    continue
                visti.add(s.key)
                righe.append([Button(text=f"{s.name} ({ch.name})",
                                     callback_data=menu(seq, f"inc:{s.key}"))])
        return Markup(inline_keyboard=(righe or [[Button(
            text="Nessun incantesimo di supporto",
            callback_data=menu(seq, "principale"))]]) + [indietro])

    righe = []
    visti = set()
    for ch in state.standing_party:
        for k in sorted(ch.inventory):
            if C.item(k).kind == "pozione" and k not in visti:
                visti.add(k)
                righe.append([Button(text=C.item(k).name,
                                     callback_data=menu(seq, f"zaino:{k}"))])
    return Markup(inline_keyboard=(righe or [[Button(
        text="Niente pozioni", callback_data=menu(seq, "principale"))]]) + [indietro])
