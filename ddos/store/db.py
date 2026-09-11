"""Schema SQLite e accesso sincrono.

Niente ORM: lo stato di gioco e' gia' un blob JSON serializzato dalle dataclass
del motore, e l'unica cosa che il database deve garantire in piu' e' che due
partite non convivano nella stessa chat. Ci pensa un indice unico parziale.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Iterable

from ddos.engine.events import Event
from ddos.engine.state import GameState

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id                TEXT PRIMARY KEY,
    chat_id           INTEGER NOT NULL,
    seed              TEXT    NOT NULL,
    state             TEXT    NOT NULL,
    phase             TEXT    NOT NULL,
    turn_seq          INTEGER NOT NULL DEFAULT 0,
    scene_message_id  INTEGER,
    deadline          REAL,
    turn_mode         TEXT    NOT NULL DEFAULT 'live',
    created_at        REAL    NOT NULL,
    updated_at        REAL    NOT NULL,
    finished_at       REAL
);

-- Una sola partita viva per chat: il resto della logica del bot si appoggia
-- a questa garanzia invece di ricontrollarla ogni volta.
CREATE UNIQUE INDEX IF NOT EXISTS games_una_per_chat
    ON games(chat_id) WHERE finished_at IS NULL;

CREATE INDEX IF NOT EXISTS games_scadenza
    ON games(deadline) WHERE finished_at IS NULL;

CREATE TABLE IF NOT EXISTS players (
    game_id     TEXT    NOT NULL,
    telegram_id INTEGER NOT NULL,
    char_id     TEXT    NOT NULL,
    username    TEXT    NOT NULL DEFAULT '',
    private_ok  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (game_id, telegram_id)
);

CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id  TEXT    NOT NULL,
    turn_seq INTEGER NOT NULL,
    kind     TEXT    NOT NULL,
    text     TEXT    NOT NULL,
    data     TEXT    NOT NULL DEFAULT '{}',
    at       REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS events_per_partita ON events(game_id, id);

CREATE TABLE IF NOT EXISTS graveyard (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id   INTEGER NOT NULL,
    game_id   TEXT    NOT NULL,
    name      TEXT    NOT NULL,
    cls       TEXT    NOT NULL,
    level     INTEGER NOT NULL,
    tier      INTEGER NOT NULL,
    seed      TEXT    NOT NULL,
    at        REAL    NOT NULL,
    UNIQUE (game_id, name)
);

CREATE INDEX IF NOT EXISTS graveyard_per_chat ON graveyard(chat_id, at);
"""


class ConflittoDiVersione(RuntimeError):
    """Qualcun altro ha scritto sulla partita nel frattempo."""


@dataclass(slots=True)
class GameRow:
    """La riga della partita piu' i metadati che il motore non conosce."""

    id: str
    chat_id: int
    seed: str
    state: GameState
    turn_seq: int
    scene_message_id: int | None
    deadline: float | None
    turn_mode: str
    finished_at: float | None

    @property
    def finished(self) -> bool:
        return self.finished_at is not None


def connect(path: str | os.PathLike[str]) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")     # letture e scritture non si bloccano
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def _row_to_game(row: sqlite3.Row) -> GameRow:
    return GameRow(
        id=row["id"],
        chat_id=row["chat_id"],
        seed=row["seed"],
        state=GameState.from_dict(json.loads(row["state"])),
        turn_seq=row["turn_seq"],
        scene_message_id=row["scene_message_id"],
        deadline=row["deadline"],
        turn_mode=row["turn_mode"],
        finished_at=row["finished_at"],
    )


# --------------------------------------------------------------------------
# Partite
# --------------------------------------------------------------------------


def create_game(conn: sqlite3.Connection, chat_id: int, state: GameState,
                turn_mode: str = "live") -> GameRow:
    now = time.time()
    conn.execute(
        "INSERT INTO games (id, chat_id, seed, state, phase, turn_seq, turn_mode,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (state.id, chat_id, state.seed, json.dumps(state.to_dict()), str(state.phase),
         state.turn_seq, turn_mode, now, now),
    )
    return GameRow(id=state.id, chat_id=chat_id, seed=state.seed, state=state,
                   turn_seq=state.turn_seq, scene_message_id=None, deadline=None,
                   turn_mode=turn_mode, finished_at=None)


def active_game(conn: sqlite3.Connection, chat_id: int) -> GameRow | None:
    row = conn.execute(
        "SELECT * FROM games WHERE chat_id=? AND finished_at IS NULL", (chat_id,)
    ).fetchone()
    return _row_to_game(row) if row else None


def game_by_id(conn: sqlite3.Connection, game_id: str) -> GameRow | None:
    row = conn.execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()
    return _row_to_game(row) if row else None


def save_game(conn: sqlite3.Connection, state: GameState, *, expected_turn_seq: int) -> None:
    """Scrittura ottimistica: fallisce se qualcuno ha gia' avanzato il turno."""
    cur = conn.execute(
        "UPDATE games SET state=?, phase=?, turn_seq=?, updated_at=?"
        " WHERE id=? AND turn_seq=?",
        (json.dumps(state.to_dict()), str(state.phase), state.turn_seq, time.time(),
         state.id, expected_turn_seq),
    )
    if cur.rowcount == 0:
        raise ConflittoDiVersione(
            f"partita {state.id}: atteso turno {expected_turn_seq}, riga gia' cambiata"
        )


def set_scene_message(conn: sqlite3.Connection, game_id: str, message_id: int | None) -> None:
    conn.execute("UPDATE games SET scene_message_id=? WHERE id=?", (message_id, game_id))


def set_deadline(conn: sqlite3.Connection, game_id: str, deadline: float | None) -> None:
    conn.execute("UPDATE games SET deadline=? WHERE id=?", (deadline, game_id))


def expired_games(conn: sqlite3.Connection, now: float) -> list[GameRow]:
    rows = conn.execute(
        "SELECT * FROM games WHERE finished_at IS NULL AND deadline IS NOT NULL"
        " AND deadline <= ?", (now,)
    ).fetchall()
    return [_row_to_game(r) for r in rows]


def finish_game(conn: sqlite3.Connection, game_id: str) -> None:
    conn.execute(
        "UPDATE games SET finished_at=?, deadline=NULL WHERE id=? AND finished_at IS NULL",
        (time.time(), game_id),
    )


# --------------------------------------------------------------------------
# Giocatori
# --------------------------------------------------------------------------


def link_player(conn: sqlite3.Connection, game_id: str, telegram_id: int,
                char_id: str, username: str = "") -> None:
    conn.execute(
        "INSERT INTO players (game_id, telegram_id, char_id, username)"
        " VALUES (?,?,?,?)"
        " ON CONFLICT(game_id, telegram_id) DO UPDATE SET char_id=excluded.char_id,"
        " username=excluded.username",
        (game_id, telegram_id, char_id, username),
    )


def char_id_for(conn: sqlite3.Connection, game_id: str, telegram_id: int) -> str | None:
    row = conn.execute(
        "SELECT char_id FROM players WHERE game_id=? AND telegram_id=?",
        (game_id, telegram_id),
    ).fetchone()
    return row["char_id"] if row else None


def telegram_id_for(conn: sqlite3.Connection, game_id: str, char_id: str) -> int | None:
    row = conn.execute(
        "SELECT telegram_id FROM players WHERE game_id=? AND char_id=?",
        (game_id, char_id),
    ).fetchone()
    return row["telegram_id"] if row else None


def active_games_for_player(conn: sqlite3.Connection, telegram_id: int) -> list[GameRow]:
    """Partite vive in cui il giocatore ha un personaggio, la piu' recente per prima."""
    rows = conn.execute(
        "SELECT g.* FROM games g JOIN players p ON p.game_id = g.id"
        " WHERE p.telegram_id = ? AND g.finished_at IS NULL"
        " ORDER BY g.updated_at DESC",
        (telegram_id,),
    ).fetchall()
    return [_row_to_game(r) for r in rows]


def mark_private_ok(conn: sqlite3.Connection, telegram_id: int) -> None:
    """Il giocatore ha aperto una chat privata: da qui in poi puo' ricevere segreti."""
    conn.execute("UPDATE players SET private_ok=1 WHERE telegram_id=?", (telegram_id,))


# --------------------------------------------------------------------------
# Cronaca e cimitero
# --------------------------------------------------------------------------


def append_events(conn: sqlite3.Connection, game_id: str, turn_seq: int,
                  events: Iterable[Event]) -> None:
    now = time.time()
    conn.executemany(
        "INSERT INTO events (game_id, turn_seq, kind, text, data, at) VALUES (?,?,?,?,?,?)",
        [(game_id, turn_seq, e.kind, e.text, json.dumps(e.data), now) for e in events],
    )


def recent_events(conn: sqlite3.Connection, game_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT kind, text, data, at FROM events WHERE game_id=? ORDER BY id DESC LIMIT ?",
        (game_id, limit),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def bury_fallen(conn: sqlite3.Connection, row: GameRow) -> int:
    """Registra i caduti. `UNIQUE(game_id, name)` rende l'operazione ripetibile."""
    now = time.time()
    caduti = [c for c in row.state.party if c.dead]
    conn.executemany(
        "INSERT OR IGNORE INTO graveyard (chat_id, game_id, name, cls, level, tier, seed, at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        [(row.chat_id, row.id, c.name, str(c.cls), c.level, row.state.tier,
          row.state.seed, now) for c in caduti],
    )
    return len(caduti)


def graveyard(conn: sqlite3.Connection, chat_id: int, limit: int = 15) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT name, cls, level, tier, seed, at FROM graveyard WHERE chat_id=?"
        " ORDER BY at DESC LIMIT ?", (chat_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def hall_stats(conn: sqlite3.Connection, chat_id: int) -> dict[str, Any]:
    morti = conn.execute(
        "SELECT COUNT(*) AS n FROM graveyard WHERE chat_id=?", (chat_id,)
    ).fetchone()["n"]
    partite = conn.execute(
        "SELECT COUNT(*) AS n FROM games WHERE chat_id=? AND finished_at IS NOT NULL",
        (chat_id,),
    ).fetchone()["n"]
    profondita = conn.execute(
        "SELECT MAX(tier) AS t FROM graveyard WHERE chat_id=?", (chat_id,)
    ).fetchone()["t"]
    return {"morti": morti, "partite": partite, "profondita_max": profondita or 0}
