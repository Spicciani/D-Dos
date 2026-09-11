"""Client da terminale.

Stesso motore del bot Telegram, nessuna dipendenza in piu': serve a giocare una
run completa mentre si sviluppa, e a tarare il bilanciamento senza chiedere un
token a BotFather. In combattimento si gioca a turni sulla stessa tastiera, come
un hot-seat del 1993.
"""

from __future__ import annotations

import argparse
import sys
import uuid

from ddos.engine import actions as A
from ddos.engine import content as C
from ddos.engine.actions import Action
from ddos.engine.dungeon import DIRECTION_NAMES
from ddos.engine.entities import ClassId
from ddos.engine.events import Event
from ddos.engine.reduce import reduce
from ddos.engine.state import GameState, Phase, new_game
from ddos.render.ansi import COLOR, PLAIN, Palette, rule
from ddos.render.views import (
    end_view, log_lines, map_view, scene_view, sheet_view,
)

CLASS_ALIASES = {
    "g": ClassId.GUERRIERO, "guerriero": ClassId.GUERRIERO, "gue": ClassId.GUERRIERO,
    "l": ClassId.LADRO, "ladro": ClassId.LADRO, "lad": ClassId.LADRO,
    "m": ClassId.MAGO, "mago": ClassId.MAGO, "mag": ClassId.MAGO,
    "c": ClassId.CHIERICO, "chierico": ClassId.CHIERICO, "chi": ClassId.CHIERICO,
}

EVENT_COLOR = {
    "critico": "bright", "colpo": "green", "mancato": "dim", "danno": "red",
    "cura": "cyan", "morte": "red", "morente": "red", "bottino": "yellow",
    "px": "yellow", "livello": "bright", "trappola": "red", "scena": "magenta",
    "errore": "red", "turno": "bold", "fine": "bright", "sussurro": "cyan",
    "incantesimo": "magenta",
}

AIUTO = """
COMANDI
  n / s / e / o        muoviti (decide chi guida)
  cerca                perlustra la stanza
  spia <dir>           ladro: sbircia la stanza accanto
  disinnesca           ladro: neutralizza la trappola
  prendi               raccogli il bottino
  prega                santuario: chiedi una grazia
  riposa               curatevi (puo' attirare nemici)
  scendi               prendi la scala
COMBATTIMENTO
  a <lettera>          attacca (es. "a A")
  pod <lettera>        guerriero: colpo poderoso
  provoca              guerriero: attira i colpi su di te
  nascondi             ladro: sparisci nell'ombra
  lancia <inc> [bers]  incantesimo (es. "lancia cura Bard")
  difendi              +2 CA fino al tuo turno
  fuggi                tenta la ritirata
SEMPRE
  scheda [nome]        mostra una scheda
  mappa                ridisegna la mappa
  usa <oggetto> [chi]  pozioni
  equip <oggetto>      cambia arma o armatura
  capo <nome>          passa il comando
  aiuto / esci

CHI AGISCE
  In combattimento gioca chi ha l'iniziativa: lo dice il prompt.
  Fuori, il comando va da se' a chi sa farlo - "spia" al Ladro,
  "lancia cura" al Chierico, "usa" a chi ha l'oggetto. Muoversi,
  riposare e scendere li decide chi guida la compagnia.
  Per scegliere tu, metti un nome davanti:
    silfa: spia n
    zilla: usa pozione_cura Bard

INCANTESIMI
  mago     dardo, sonno, scudo_arcano, dardi_multipli (liv. 3)
  chierico cura, benedizione, scacciare
""".strip()


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


class Console:
    def __init__(self, palette: Palette) -> None:
        self.p = palette

    def lines(self, righe: list[str], colore: str = "") -> None:
        codice = getattr(self.p, colore, "") if colore else ""
        for riga in righe:
            print(f"{codice}{riga}{self.p.reset}" if codice else riga)

    def events(self, eventi: list[Event]) -> None:
        for evento in eventi:
            for riga in log_lines([evento], limit=99):
                colore = getattr(self.p, EVENT_COLOR.get(evento.kind, ""), "")
                print(f"{colore}{riga}{self.p.reset}" if colore else riga)

    def error(self, testo: str) -> None:
        print(f"{self.p.red}x {testo}{self.p.reset}")

    def rule(self) -> None:
        print(f"{self.p.dim}{rule()}{self.p.reset}")


# --------------------------------------------------------------------------
# Creazione della compagnia
# --------------------------------------------------------------------------


def parse_party(spec: str) -> list[tuple[str, ClassId]]:
    """`"Grommash:g,Silfa:ladro"` -> [(nome, classe), ...]."""
    membri = []
    for pezzo in spec.split(","):
        pezzo = pezzo.strip()
        if not pezzo:
            continue
        nome, _, classe = pezzo.partition(":")
        chiave = classe.strip().lower()
        if chiave not in CLASS_ALIASES:
            raise SystemExit(f"classe sconosciuta: {classe!r} (usa g/l/m/c)")
        membri.append((nome.strip() or "Anonimo", CLASS_ALIASES[chiave]))
    if not membri:
        raise SystemExit("serve almeno un avventuriero")
    return membri


def chiedi_party(console: Console) -> list[tuple[str, ClassId]]:
    print("Crea la compagnia. Invio a vuoto per finire.\n")
    for cid, cdef in C.CLASSES.items():
        print(f"  {str(cid)[0]} = {cdef.name:<10} {cdef.blurb}")
    print()
    membri: list[tuple[str, ClassId]] = []
    while len(membri) < 6:
        try:
            riga = input(f"[{len(membri) + 1}] nome:classe > ").strip()
        except EOFError:
            break
        if not riga:
            break
        try:
            membri.extend(parse_party(riga))
        except SystemExit as exc:
            console.error(str(exc))
    if not membri:
        membri = [("Grommash", ClassId.GUERRIERO), ("Silfa", ClassId.LADRO),
                  ("Zilla", ClassId.MAGO), ("Bard", ClassId.CHIERICO)]
        print("\nCompagnia di riserva arruolata d'ufficio.")
    return membri


# --------------------------------------------------------------------------
# Comandi
# --------------------------------------------------------------------------


def trova_personaggio(state: GameState, testo: str):
    testo = testo.strip().lower()
    if not testo:
        return None
    for ch in state.party:
        if ch.name.lower() == testo or ch.id.lower() == testo:
            return ch
    for ch in state.party:
        if ch.name.lower().startswith(testo):
            return ch
    return None


def trova_mostro(state: GameState, testo: str):
    testo = testo.strip().upper()
    if not state.combat:
        return None
    vivi = state.live_monsters
    if not testo:
        return vivi[0] if vivi else None
    for m in vivi:
        if m.label == testo or m.id.upper() == testo:
            return m
    for m in vivi:
        if m.name.upper().startswith(testo):
            return m
    return None


# Gruppi di alias: unica fonte di verita' per la scelta dell'attore e per il
# riconoscimento del comando, cosi' le due cose non possono divergere.
CMD_SPIA = ("spia", "avanscoperta")
CMD_DISINNESCA = ("disinnesca", "disarma")
CMD_LANCIA = ("lancia", "inc")
CMD_USA = ("usa",)
CMD_EQUIP = ("equip", "equipaggia")
CMD_DEL_CAPO = ("riposa", "riposo", "scendi")


def _candidati(state: GameState) -> list:
    """Chi puo' agire, con chi guida in testa."""
    in_piedi = state.standing_party
    capo = state.leader
    if capo is not None and capo.alive:
        return [capo] + [c for c in in_piedi if c is not capo]
    return in_piedi


def attore_per(state: GameState, comando: str, resto: list[str]):
    """Chi esegue il comando, e perche' nessuno puo' farlo.

    In combattimento non c'e' scelta: agisce chi ha l'iniziativa. Fuori, il
    client sceglie chi *sa* fare quella cosa invece di attribuire tutto a chi
    guida, altrimenti l'avanscoperta del Ladro e le cure del Chierico sarebbero
    irraggiungibili a meno di passare il comando avanti e indietro.

    Ritorna `(personaggio, motivo)`: uno dei due e' sempre vuoto.
    """
    if state.phase is Phase.COMBATTIMENTO and state.combat:
        return state.char(state.combat.current_id), ""

    candidati = _candidati(state)
    if not candidati:
        return None, "Non c'e' nessuno in grado di agire."

    if comando in DIRECTION_NAMES or comando in CMD_DEL_CAPO:
        return candidati[0], ""

    if comando in CMD_SPIA or comando in CMD_DISINNESCA:
        ladro = next((c for c in candidati if c.cls is ClassId.LADRO), None)
        if ladro is None:
            return None, "Serve un Ladro in piedi: nessun altro sa farlo."
        return ladro, ""

    if comando in CMD_LANCIA and resto:
        spell = C.SPELLS.get(resto[0].lower())
        if spell is None:
            return candidati[0], ""  # il nome sbagliato lo segnala il chiamante
        lanciatore = next(
            (c for c in candidati
             if c.cls is spell.cls and c.level >= spell.min_level and c.spell_slots > 0),
            None,
        )
        if lanciatore is None:
            classe = C.CLASSES[spell.cls].name
            return None, (f"Nessuno puo' lanciare {spell.name}: serve un {classe} "
                          f"in piedi con uno slot libero.")
        return lanciatore, ""

    if (comando in CMD_USA or comando in CMD_EQUIP) and resto:
        chiave = resto[0].lower()
        portatore = next((c for c in candidati if chiave in c.inventory), None)
        if portatore is None:
            nome = C.ITEMS[chiave].name if chiave in C.ITEMS else chiave
            return None, f"Nessuno ha {nome} nello zaino."
        return portatore, ""

    return candidati[0], ""


def _prefisso_attore(state: GameState, riga: str, console: Console):
    """Riconosce `nome: comando`. Ritorna `(personaggio, riga ripulita)`."""
    testa = riga.split(" ", 1)[0]
    if ":" not in testa:
        return None, riga
    prefisso, _, coda = riga.partition(":")
    scelto = trova_personaggio(state, prefisso)
    if scelto is None:
        console.error(f"Non c'e' nessuno che si chiami '{prefisso.strip()}'.")
        return None, ""
    return scelto, coda.strip()


def costruisci_azione(state: GameState, riga: str, console: Console) -> Action | None:
    """Traduce una riga di comando in un'azione. None = comando locale, gia' gestito."""
    forzato, riga = _prefisso_attore(state, riga, console)
    if not riga:
        if forzato is not None:
            console.error(f"E {forzato.name} cosa dovrebbe fare?")
        return None

    parti = riga.split()
    comando = parti[0].lower()
    resto = parti[1:]

    attore, motivo = attore_per(state, comando, resto)
    if forzato is not None:
        if state.phase is Phase.COMBATTIMENTO:
            console.error("In combattimento l'ordine di iniziativa non si scavalca.")
            return None
        attore, motivo = forzato, ""

    # I comandi che non toccano lo stato si leggono anche a compagnia a terra.
    if comando in ("aiuto", "help", "?"):
        print(AIUTO)
        return None
    if comando == "mappa":
        console.lines(map_view(state), "dim")
        return None
    if comando == "scheda":
        bersaglio = (trova_personaggio(state, " ".join(resto)) or attore
                     or (state.party[0] if state.party else None))
        if bersaglio is None:
            console.error("Nessuna scheda da mostrare.")
            return None
        console.lines(sheet_view(bersaglio))
        return None
    if comando in ("esci", "quit", "q"):
        raise SystemExit(0)

    if attore is None:
        console.error(motivo or "Non c'e' nessuno in grado di agire.")
        return None
    aid = attore.id

    if comando in DIRECTION_NAMES:
        return Action(A.MOVE, aid, value=comando)
    if comando in ("cerca", "perlustra"):
        return Action(A.SEARCH, aid)
    if comando in CMD_SPIA:
        if not resto or resto[0].lower() not in DIRECTION_NAMES:
            console.error("Serve una direzione: spia n|s|e|o")
            return None
        return Action(A.SCOUT, aid, value=resto[0].lower())
    if comando in CMD_DISINNESCA:
        return Action(A.DISARM, aid)
    if comando in ("prendi", "raccogli"):
        return Action(A.TAKE, aid)
    if comando == "prega":
        return Action(A.PRAY, aid)
    if comando in ("riposa", "riposo"):
        return Action(A.REST, aid)
    if comando == "scendi":
        return Action(A.DESCEND, aid)

    if comando in ("a", "attacca", "att"):
        mostro = trova_mostro(state, resto[0] if resto else "")
        if mostro is None:
            console.error("Nessun bersaglio.")
            return None
        return Action(A.ATTACK, aid, mostro.id)
    if comando in ("pod", "poderoso"):
        mostro = trova_mostro(state, resto[0] if resto else "")
        if mostro is None:
            console.error("Nessun bersaglio.")
            return None
        return Action(A.POWER, aid, mostro.id)
    if comando in ("provoca", "provocare"):
        return Action(A.TAUNT, aid)
    if comando in ("nascondi", "nasconditi"):
        return Action(A.HIDE, aid)
    if comando in ("difendi", "guardia"):
        return Action(A.DEFEND, aid)
    if comando in ("fuggi", "ritirata"):
        return Action(A.FLEE, aid)

    if comando in CMD_LANCIA:
        if not resto:
            console.error(f"Quale incantesimo? ({_incantesimi_disponibili(state, attore)})")
            return None
        chiave = resto[0].lower()
        spell = C.SPELLS.get(chiave)
        if spell is None:
            console.error(f"Incantesimo sconosciuto: {chiave}")
            return None
        arg = " ".join(resto[1:])
        if spell.target == C.TARGET_NEMICO:
            mostro = trova_mostro(state, arg)
            bersaglio = mostro.id if mostro else ""
        elif spell.target == C.TARGET_ALLEATO:
            alleato = trova_personaggio(state, arg) or attore
            bersaglio = alleato.id
        else:
            bersaglio = ""
        return Action(A.CAST, aid, bersaglio, chiave)

    if comando in CMD_USA:
        if not resto:
            console.error("Quale oggetto?")
            return None
        chiave = resto[0].lower()
        alleato = trova_personaggio(state, " ".join(resto[1:])) or attore
        return Action(A.USE, aid, alleato.id, chiave)
    if comando in CMD_EQUIP:
        if not resto:
            console.error("Quale oggetto?")
            return None
        return Action(A.EQUIP, aid, value=resto[0].lower())
    if comando in ("capo", "guida"):
        nuovo = trova_personaggio(state, " ".join(resto))
        if nuovo is None:
            console.error("Chi?")
            return None
        return Action(A.SET_LEADER, aid, nuovo.id)

    console.error(f"Non conosco '{comando}'. Prova 'aiuto'.")
    return None


def _incantesimi_disponibili(state: GameState, attore) -> str:
    """Cosa puo' lanciare la compagnia adesso, non solo chi ha in mano il turno."""
    if state.phase is Phase.COMBATTIMENTO and attore is not None:
        chiavi = [s.key for s in C.spells_for(attore.cls, attore.level)]
    else:
        chiavi = []
        for ch in _candidati(state):
            if ch.spell_slots <= 0:
                continue
            chiavi += [s.key for s in C.spells_for(ch.cls, ch.level) if s.key not in chiavi]
    return ", ".join(chiavi) or "nessuno"


# --------------------------------------------------------------------------
# Ciclo di gioco
# --------------------------------------------------------------------------


def prompt_label(state: GameState) -> str:
    attore, _ = attore_per(state, "", [])
    nome = attore.name if attore else "?"
    if state.phase is Phase.COMBATTIMENTO and state.combat:
        return f"[R{state.combat.round} {nome}]> "
    return f"[{nome}]> "


def gioca(state: GameState, console: Console) -> GameState:
    state, eventi = reduce(state, Action(A.BEGIN))
    console.lines(scene_view(state))
    console.rule()
    console.events(eventi)

    while not state.over:
        console.rule()
        try:
            riga = input(prompt_label(state)).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return state
        if not riga:
            console.lines(scene_view(state))
            continue

        azione = costruisci_azione(state, riga, console)
        if azione is None:
            continue

        nuovo, eventi = reduce(state, azione)
        console.events(eventi)
        if nuovo is not state:
            state = nuovo
            console.lines(scene_view(state))

    console.rule()
    console.lines(end_view(state), "bright" if state.phase is Phase.VITTORIA else "red")
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ddos-play",
        description="D-DOS: dungeon crawl testuale. Stesso motore del bot Telegram.",
    )
    parser.add_argument("--seed", default="", help="seme della run (condivisibile)")
    parser.add_argument("--party", default="",
                        help='compagnia, es. "Grommash:g,Silfa:l,Zilla:m,Bard:c"')
    parser.add_argument("--no-color", action="store_true", help="niente colori ANSI")
    args = parser.parse_args(argv)

    console = Console(PLAIN if args.no_color else COLOR)
    seed = args.seed or uuid.uuid4().hex[:8].upper()

    membri = parse_party(args.party) if args.party else chiedi_party(console)

    state = new_game(game_id="cli", seed=seed)
    for indice, (nome, cls) in enumerate(membri, start=1):
        state, eventi = reduce(state, Action(A.JOIN, f"pg{indice}", nome, str(cls)))
        console.events(eventi)

    print()
    state = gioca(state, console)
    print(f"\nSeme di questa run: {state.seed}")
    return 0 if state.phase is Phase.VITTORIA else 1


if __name__ == "__main__":
    sys.exit(main())
