"""Azioni: l'unico ingresso al motore.

Campi volutamente piatti e testuali: un'azione entra in 64 byte di
`callback_data` di Telegram e si logga in chiaro nell'event log.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- lobby ---
JOIN = "join"                # value = classe, target = nome
LEAVE = "leave"
SET_LEADER = "leader"        # target = id personaggio
BEGIN = "begin"

# --- esplorazione ---
MOVE = "move"                # value = n|s|e|o
SEARCH = "search"            # cerca trappole e passaggi
DISARM = "disarm"            # ladro: disinnesca la trappola trovata
SCOUT = "scout"              # ladro: sbircia la stanza accanto (value = direzione)
TAKE = "take"                # raccogli il bottino della stanza
PRAY = "pray"                # santuario
REST = "rest"                # riposo breve
DESCEND = "descend"          # scendi di livello

# --- combattimento ---
ATTACK = "attack"            # target = id mostro
POWER = "power"              # guerriero: colpo poderoso
TAUNT = "taunt"              # guerriero: provocare
HIDE = "hide"                # ladro: nascondersi
CAST = "cast"                # value = chiave incantesimo, target = id bersaglio
DEFEND = "defend"
FLEE = "flee"

# --- ovunque ---
USE = "use"                  # value = chiave oggetto, target = id destinatario
EQUIP = "equip"              # value = chiave oggetto
TICK = "tick"                # il tempo scade: azione di default per chi tocca

EXPLORATION_ACTIONS = frozenset(
    {MOVE, SEARCH, DISARM, SCOUT, TAKE, PRAY, REST, DESCEND, USE, EQUIP, CAST}
)
COMBAT_ACTIONS = frozenset({ATTACK, POWER, TAUNT, HIDE, CAST, DEFEND, FLEE, USE})
LOBBY_ACTIONS = frozenset({JOIN, LEAVE, SET_LEADER, BEGIN})


@dataclass(frozen=True, slots=True)
class Action:
    kind: str
    actor: str = ""      # id del personaggio che agisce ("" = sistema)
    target: str = ""     # id del bersaglio, o nome in fase di lobby
    value: str = ""      # direzione, chiave incantesimo, chiave oggetto...

    def encode(self) -> str:
        """Serializza per il `callback_data` di Telegram."""
        return "|".join((self.kind, self.actor, self.target, self.value))

    @classmethod
    def decode(cls, raw: str) -> "Action":
        parts = (raw.split("|") + ["", "", ""])[:4]
        return cls(kind=parts[0], actor=parts[1], target=parts[2], value=parts[3])
