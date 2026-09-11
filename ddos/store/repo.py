"""Accesso asincrono al database.

SQLite e' sincrono e le connessioni non attraversano i thread: ogni operazione
apre la sua connessione dentro `asyncio.to_thread`, cosi' il loop del bot non si
blocca mai e non esiste stato condiviso tra thread. Con WAL attivo il costo di
aprire una connessione e' trascurabile rispetto a una chiamata a Telegram.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any, Callable, TypeVar

from ddos.engine.events import Event
from ddos.engine.state import GameState, new_game
from ddos.store import db

T = TypeVar("T")

#: Alfabeto dei semi: niente vocali, niente caratteri ambigui da leggere ad alta voce.
_SEED_CONS = "BCDFGHJKLMNPQRSTVWXZ"
_SEED_VOC = "AEIOU"


def genera_seme(rng: Any = None) -> str:
    """Un seme pronunciabile e dettabile a voce, tipo `KORVAX-4471`."""
    import random

    rng = rng or random.SystemRandom()
    parola = "".join(
        rng.choice(_SEED_CONS if i % 2 == 0 else _SEED_VOC) for i in range(6)
    )
    return f"{parola}-{rng.randint(1000, 9999)}"


class Repo:
    """Facciata asincrona. Tutti i metodi sono sicuri da chiamare dal loop."""

    def __init__(self, path: str | os.PathLike[str] = "ddos.db") -> None:
        self.path = str(path)

    # --- infrastruttura ----------------------------------------------------
    def _sync(self, fn: Callable[[Any], T]) -> T:
        conn = db.connect(self.path)
        try:
            return fn(conn)
        finally:
            conn.close()

    async def _call(self, fn: Callable[[Any], T]) -> T:
        return await asyncio.to_thread(self._sync, fn)

    async def setup(self) -> None:
        await self._call(db.init_db)

    # --- partite -----------------------------------------------------------
    async def create_game(self, chat_id: int, seed: str = "",
                          turn_mode: str = "live") -> db.GameRow:
        state = new_game(game_id=uuid.uuid4().hex[:12], seed=seed or genera_seme(),
                         turn_mode=turn_mode)
        return await self._call(lambda c: db.create_game(c, chat_id, state, turn_mode))

    async def active_game(self, chat_id: int) -> db.GameRow | None:
        return await self._call(lambda c: db.active_game(c, chat_id))

    async def game_by_id(self, game_id: str) -> db.GameRow | None:
        return await self._call(lambda c: db.game_by_id(c, game_id))

    async def save(self, state: GameState, *, expected_turn_seq: int,
                   events: list[Event] | None = None) -> None:
        """Salva stato ed eventi in una sola transazione: o tutto, o niente."""

        def opera(conn):
            conn.execute("BEGIN IMMEDIATE")
            try:
                db.save_game(conn, state, expected_turn_seq=expected_turn_seq)
                if events:
                    db.append_events(conn, state.id, state.turn_seq, events)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

        await self._call(opera)

    async def set_scene_message(self, game_id: str, message_id: int | None) -> None:
        await self._call(lambda c: db.set_scene_message(c, game_id, message_id))

    async def set_deadline(self, game_id: str, deadline: float | None) -> None:
        await self._call(lambda c: db.set_deadline(c, game_id, deadline))

    async def expired_games(self, now: float) -> list[db.GameRow]:
        return await self._call(lambda c: db.expired_games(c, now))

    async def finish(self, row: db.GameRow) -> int:
        """Chiude la partita e seppellisce i caduti. Ripetibile senza danni."""

        def opera(conn):
            conn.execute("BEGIN IMMEDIATE")
            try:
                caduti = db.bury_fallen(conn, row)
                db.finish_game(conn, row.id)
                conn.execute("COMMIT")
                return caduti
            except Exception:
                conn.execute("ROLLBACK")
                raise

        return await self._call(opera)

    # --- giocatori ---------------------------------------------------------
    async def link_player(self, game_id: str, telegram_id: int, char_id: str,
                          username: str = "") -> None:
        await self._call(lambda c: db.link_player(c, game_id, telegram_id, char_id, username))

    async def char_id_for(self, game_id: str, telegram_id: int) -> str | None:
        return await self._call(lambda c: db.char_id_for(c, game_id, telegram_id))

    async def telegram_id_for(self, game_id: str, char_id: str) -> int | None:
        return await self._call(lambda c: db.telegram_id_for(c, game_id, char_id))

    async def active_games_for_player(self, telegram_id: int) -> list[db.GameRow]:
        return await self._call(lambda c: db.active_games_for_player(c, telegram_id))

    async def mark_private_ok(self, telegram_id: int) -> None:
        await self._call(lambda c: db.mark_private_ok(c, telegram_id))

    # --- cronaca e cimitero ------------------------------------------------
    async def recent_events(self, game_id: str, limit: int = 20) -> list[dict[str, Any]]:
        return await self._call(lambda c: db.recent_events(c, game_id, limit))

    async def graveyard(self, chat_id: int, limit: int = 15) -> list[dict[str, Any]]:
        return await self._call(lambda c: db.graveyard(c, chat_id, limit))

    async def hall_stats(self, chat_id: int) -> dict[str, Any]:
        return await self._call(lambda c: db.hall_stats(c, chat_id))
