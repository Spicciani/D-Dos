"""Dallo stato di gioco alle schermate.

Ogni funzione ritorna una lista di righe gia' tagliate a 32 colonne. Chi chiama
decide se avvolgerle in un `<pre>` di Telegram o stamparle sul terminale.
"""

from __future__ import annotations

import html

from ddos.engine import content as C
from ddos.engine import rules as R
from ddos.engine.dungeon import DIRECTION_NAMES, OPPOSITE, Level, RoomKind
from ddos.engine.entities import ABILITY_ORDER, Character, Status
from ddos.engine.events import Event
from ddos.engine.state import GameState, Phase
from ddos.render.ansi import INNER, WIDTH, bar, box, clip, columns, kv, title_screen, wrap

#: Prefisso per tipo di evento: un log da roguelike si legge dal margine.
PREFIX: dict[str, str] = {
    "colpo": ">", "critico": "*", "mancato": "-", "danno": " ", "cura": "+",
    "morte": "X", "morente": "!", "salvezza": "~", "incantesimo": "@",
    "condizione": "~", "bottino": "$", "px": "^", "livello": "^", "trappola": "^",
    "movimento": ">", "info": " ", "scena": "=", "errore": "x", "turno": ">",
    "fine": "=", "sussurro": "\"",
}

CLASS_ABBR = {"guerriero": "GUE", "ladro": "LAD", "mago": "MAG", "chierico": "CHI"}

COND_ABBR = {
    "difesa": "DIF", "scudo": "SCU", "benedetto": "BEN", "veleno": "VEL",
    "stordito": "STO", "nascosto": "NAS", "provocato": "PRV",
}


def esc(text: str) -> str:
    """Per il parse_mode HTML di Telegram."""
    return html.escape(text, quote=False)


def pre(lines: list[str]) -> str:
    """Blocco monospace pronto da mandare a Telegram."""
    return "<pre>" + esc("\n".join(lines)) + "</pre>"


# --------------------------------------------------------------------------
# Mappa
# --------------------------------------------------------------------------


def map_lines(level: Level, room_id: str) -> list[str]:
    """Griglia del livello. Si vede solo dove si e' stati, piu' le porte accanto."""
    visitate = {r.id for r in level.rooms.values() if r.visited}
    note = set(visitate)
    for rid in visitate:
        note.update(level.rooms[rid].exits.values())

    per_coord = {(r.x, r.y): r for r in level.rooms.values()}
    righe: list[str] = []

    for y in range(level.height):
        riga_stanze = ""
        riga_legami = ""
        for x in range(level.width):
            stanza = per_coord.get((x, y))
            if stanza is None or stanza.id not in note:
                riga_stanze += " "
                riga_legami += " "
            else:
                if stanza.id == room_id:
                    riga_stanze += "@"
                elif stanza.visited:
                    riga_stanze += stanza.glyph
                else:
                    riga_stanze += "?"
                sotto = stanza.exits.get("s")
                riga_legami += "|" if sotto and sotto in note else " "

            # collegamento orizzontale verso la cella a destra
            if x < level.width - 1:
                destra = stanza.exits.get("e") if stanza else None
                collegate = bool(destra) and destra in note and stanza.id in note
                riga_stanze += "---" if collegate else "   "
                riga_legami += "   "
        righe.append(riga_stanze.rstrip())
        if y < level.height - 1:
            righe.append(riga_legami.rstrip())

    # Via le righe vuote in cima e in fondo: la mappa non deve galleggiare.
    while righe and not righe[0].strip():
        righe.pop(0)
    while righe and not righe[-1].strip():
        righe.pop()
    return righe or ["(mappa vuota)"]


MAP_LEGEND = "@ voi  < in  > giu  ! nemici"
MAP_LEGEND2 = "$ oro  ^ trappola  + altare"


def map_view(state: GameState) -> list[str]:
    if state.level is None:
        return box(["Nessuna mappa."], "MAPPA")
    corpo = map_lines(state.level, state.room_id)
    corpo += ["", MAP_LEGEND, MAP_LEGEND2]
    return box(corpo, f"MAPPA  LIVELLO {state.tier}")


# --------------------------------------------------------------------------
# Compagnia
# --------------------------------------------------------------------------


def _cond_tag(ch: Character) -> str:
    if not ch.conditions:
        return ""
    return " " + ",".join(COND_ABBR.get(k, k[:3].upper()) for k in sorted(ch.conditions))


def party_lines(state: GameState, *, active_id: str = "") -> list[str]:
    righe: list[str] = []
    for ch in state.party:
        marcatore = ">" if ch.id == active_id else " "
        capo = "*" if ch.id == state.leader_id else " "
        nome = clip(ch.name, 8).ljust(8)
        classe = CLASS_ABBR.get(str(ch.cls), "???")
        if ch.status is Status.MORTO:
            righe.append(f"{marcatore}{capo}{nome} {classe} MORTO")
            continue
        if ch.status is Status.MORENTE:
            stato = "STAB" if ch.death_successes >= 3 else f"{ch.death_failures}/3"
            righe.append(f"{marcatore}{capo}{nome} {classe} AGONIA {stato}")
            continue
        righe.append(f"{marcatore}{capo}{nome} {classe} L{ch.level} {bar(ch.hp, ch.max_hp, 6)}")
        dettaglio = f"    {ch.hp}/{ch.max_hp} CA{R.armor_class(ch)}"
        if ch.spell_slots_max:
            dettaglio += f" slot{ch.spell_slots}/{ch.spell_slots_max}"
        righe.append(clip(dettaglio + _cond_tag(ch)))
    return righe


def party_view(state: GameState, *, active_id: str = "") -> list[str]:
    corpo = party_lines(state, active_id=active_id)
    corpo.append(kv("Oro comune", str(state.gold)))
    return box(corpo, "COMPAGNIA")


# --------------------------------------------------------------------------
# Stanza e combattimento
# --------------------------------------------------------------------------


def exits_line(state: GameState) -> str:
    stanza = state.room
    if stanza is None or not stanza.exits:
        return "Nessuna uscita."
    nomi = []
    for direzione in ("n", "s", "e", "o"):
        if direzione in stanza.exits:
            visitata = state.level.rooms[stanza.exits[direzione]].visited
            nomi.append(DIRECTION_NAMES[direzione] + ("'" if visitata else ""))
    return "Uscite: " + ", ".join(nomi)


def room_view(state: GameState) -> list[str]:
    stanza = state.room
    if stanza is None:
        return box(["Nel nulla."], "STANZA")
    corpo = wrap(stanza.flavor)
    if stanza.kind == RoomKind.TESORO and (stanza.loot or stanza.gold):
        bottino = ", ".join(C.item(k).name for k in stanza.loot)
        corpo += ["", clip(f"Bottino: {bottino}" if bottino else "Bottino: monete")]
    if stanza.kind == RoomKind.TRAPPOLA and stanza.trap_found and not stanza.cleared:
        corpo += ["", clip(f"! Trappola: {C.TRAPS[stanza.trap].name}")]
    if stanza.kind == RoomKind.SANTUARIO and not stanza.cleared:
        corpo += ["", "Un altare attende."]
    if stanza.kind == RoomKind.SCALA:
        corpo += ["", "Una scala scende."]
    corpo += ["", exits_line(state)]
    return box(corpo, stanza.label.upper())


def monsters_view(state: GameState) -> list[str]:
    if not state.combat:
        return []
    corpo = []
    for m in state.combat.monsters:
        if m.alive:
            tag = ",".join(COND_ABBR.get(k, k[:3].upper()) for k in sorted(m.conditions))
            riga = f"{m.label} {clip(m.name, 13).ljust(13)} {bar(m.hp, m.max_hp, 6)}"
            corpo.append(clip(riga + (f" {tag}" if tag else "")))
        else:
            corpo.append(f"{m.label} {clip(m.name, 13).ljust(13)} -- abbattuto")
    turno = state.combatant(state.combat.current_id)
    nome = getattr(turno, "name", getattr(turno, "display", "?"))
    corpo.append("")
    corpo.append(kv(f"Round {state.combat.round}", f"turno: {clip(nome, 12)}"))
    return box(corpo, "NEMICI")


# --------------------------------------------------------------------------
# Scheda personaggio
# --------------------------------------------------------------------------


def sheet_view(ch: Character, *, state: GameState | None = None) -> list[str]:
    cdef = C.CLASSES[ch.cls]
    corpo = [
        kv(f"{ch.name}", f"{cdef.name} L{ch.level}"),
        kv("PF", f"{ch.hp}/{ch.max_hp}  {bar(ch.hp, ch.max_hp, 8)}"),
        kv("Classe Armatura", str(R.armor_class(ch))),
        kv("Attacco", f"{R.attack_bonus(ch):+d}"),
    ]
    mancanti = C.xp_to_next(ch.xp)
    corpo.append(kv("PX", f"{ch.xp}" + (f" (+{mancanti} al liv.)" if mancanti else " MAX")))
    if ch.spell_slots_max:
        corpo.append(kv("Slot incantesimo", f"{ch.spell_slots}/{ch.spell_slots_max}"))

    corpo.append("")
    corpo += columns([f"{a} {ch.abilities[a]:>2} ({ch.mod(a):+d})" for a in ABILITY_ORDER], 2)

    corpo.append("")
    arma = R.weapon_of(ch)
    corpo.append(kv("Arma", f"{clip(arma.name, 14)} {arma.damage}"))
    armatura = C.ITEMS.get(ch.armor)
    if armatura:
        corpo.append(kv("Armatura", f"{clip(armatura.name, 12)} +{armatura.ac_bonus}"))

    incantesimi = C.spells_for(ch.cls, ch.level)
    if incantesimi:
        corpo.append("")
        corpo.append("Incantesimi:")
        corpo += [f" - {clip(s.name, INNER - 3)}" for s in incantesimi]

    if cdef.talents:
        corpo.append("")
        corpo.append("Talenti:")
        for t in cdef.talents:
            pezzi = wrap(C.TALENTS[t], INNER - 3)
            corpo.append(f" - {pezzi[0]}")
            corpo += [f"   {p}" for p in pezzi[1:]]

    corpo.append("")
    corpo.append("Zaino:")
    if ch.inventory:
        corpo += [f" - {clip(C.item(k).name, INNER - 7)} x{q}"
                  for k, q in sorted(ch.inventory.items())]
    else:
        corpo.append(" (vuoto)")

    if ch.status is not Status.VIVO:
        corpo.append("")
        corpo.append("*** " + ("MORTO" if ch.dead else "IN AGONIA") + " ***")
    return box(corpo, "SCHEDA")


# --------------------------------------------------------------------------
# Log e schermate complete
# --------------------------------------------------------------------------


def log_lines(events: list[Event], limit: int = 14) -> list[str]:
    righe: list[str] = []
    for evento in events:
        if evento.kind == "sussurro":
            continue  # va in privato, non nel log pubblico
        prefisso = PREFIX.get(evento.kind, " ")
        pezzi = wrap(evento.text, INNER - 2)
        righe.append(f"{prefisso} {pezzi[0]}")
        righe += [f"  {p}" for p in pezzi[1:]]
    return righe[-limit:] if len(righe) > limit else righe


def log_view(events: list[Event], limit: int = 14) -> list[str]:
    righe = log_lines(events, limit)
    return box(righe or ["..."], "CRONACA")


def lobby_view(state: GameState) -> list[str]:
    corpo: list[str] = []
    if state.party:
        for ch in state.party:
            capo = "*" if ch.id == state.leader_id else " "
            corpo.append(f"{capo}{clip(ch.name, 10).ljust(10)} "
                         f"{clip(C.CLASSES[ch.cls].name, 9).ljust(9)} {ch.hp} PF")
    else:
        corpo.append("Nessun avventuriero. Ancora.")
    corpo.append("")
    corpo.append(kv("Posti", f"{len(state.party)}/6"))
    return title_screen(f"seme {state.seed}") + [""] + box(corpo, "TAVERNA")


def end_view(state: GameState) -> list[str]:
    vinto = state.phase is Phase.VITTORIA
    titolo = "VITTORIA" if vinto else "FINE DELLA SPEDIZIONE"
    corpo = wrap(
        "Il Signore delle Ossa e' caduto. La compagnia risale alla luce."
        if vinto else
        "Il sotterraneo si richiude. Nessuno risalira' a raccontarlo."
    )
    corpo.append("")
    for ch in state.party:
        esito = "MORTO" if ch.dead else ("in agonia" if ch.status is Status.MORENTE
                                         else f"{ch.hp}/{ch.max_hp} PF")
        corpo.append(kv(f"{clip(ch.name, 10)} L{ch.level}", esito))
    corpo.append("")
    corpo.append(kv("Livello raggiunto", str(state.tier)))
    corpo.append(kv("Oro", str(state.gold)))
    corpo.append(kv("Nemici abbattuti", str(sum(c.kills for c in state.party))))
    corpo.append(kv("Seme", state.seed))
    return box(corpo, titolo)


def scene_view(state: GameState, events: list[Event] | None = None) -> list[str]:
    """La schermata principale: dove siete, chi siete, cosa e' appena successo."""
    if state.phase is Phase.LOBBY:
        return lobby_view(state)
    if state.over:
        return end_view(state)

    righe: list[str] = []
    if state.phase is Phase.COMBATTIMENTO:
        attivo = state.combat.current_id if state.combat else ""
        righe += monsters_view(state)
        righe += party_view(state, active_id=attivo)
    else:
        righe += room_view(state)
        righe += map_view(state)
        righe += party_view(state)
    if events:
        righe += log_view(events)
    return righe
