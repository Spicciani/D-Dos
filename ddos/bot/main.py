"""Avvio del bot.

Serve solo `BOT_TOKEN` da @BotFather. Il database SQLite si crea da solo al
primo avvio; una partita interrotta da un riavvio riprende dallo stato salvato,
perche' il motore non tiene nulla in memoria.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from ddos.bot.handlers import actions as handler_azioni
from ddos.bot.handlers import commands as handler_comandi
from ddos.bot.handlers import private as handler_privato
from ddos.bot.session import GameService, ticker
from ddos.store.repo import Repo

log = logging.getLogger("ddos")

COMANDI = [
    BotCommand(command="nuova", description="apri una spedizione"),
    BotCommand(command="entra", description="crea il tuo personaggio"),
    BotCommand(command="via", description="si scende"),
    BotCommand(command="scheda", description="la tua scheda, in privato"),
    BotCommand(command="mappa", description="la mappa del livello"),
    BotCommand(command="stato", description="ridisegna la scena"),
    BotCommand(command="cimitero", description="chi non e' tornato"),
    BotCommand(command="abbandona", description="chiudi la spedizione"),
    BotCommand(command="aiuto", description="come si gioca"),
]


async def run(token: str, db_path: str) -> None:
    repo = Repo(db_path)
    await repo.setup()

    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    service = GameService(bot, repo)

    dispatcher = Dispatcher(service=service, repo=repo)
    dispatcher.include_router(handler_privato.router)
    dispatcher.include_router(handler_comandi.router)
    dispatcher.include_router(handler_azioni.router)

    await bot.set_my_commands(COMANDI)
    sorvegliante = asyncio.create_task(ticker(service))
    try:
        me = await bot.get_me()
        log.info("D-DOS in ascolto come @%s", me.username)
        await dispatcher.start_polling(bot, handle_signals=False)
    finally:
        sorvegliante.cancel()
        await asyncio.gather(sorvegliante, return_exceptions=True)
        await bot.session.close()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("DDOS_LOG", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        print("Manca BOT_TOKEN. Chiedilo a @BotFather e mettilo in un file .env,\n"
              "oppure esportalo:  export BOT_TOKEN=123456:ABC...", file=sys.stderr)
        return 2

    db_path = os.environ.get("DDOS_DB", "ddos.db")
    try:
        asyncio.run(run(token, db_path))
    except KeyboardInterrupt:
        log.info("chiusura richiesta")
    return 0


if __name__ == "__main__":
    sys.exit(main())
