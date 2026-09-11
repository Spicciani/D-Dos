"""Chat privata: schede, segreti, e il primo contatto.

Serve soprattutto ad aprire il canale: Telegram non permette a un bot di
scrivere a qualcuno che non gli ha mai parlato, quindi finche' un giocatore non
fa /start qui, l'avanscoperta del Ladro non ha dove arrivare.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from ddos.render.views import esc, pre, sheet_view
from ddos.store.repo import Repo

router = Router(name="privato")

BENVENUTO = """<b>D-DOS</b>

Sono il narratore. Il gioco si svolge nel vostro gruppo: aggiungimi la',
poi /nuova per aprire una spedizione.

Questa chat serve per le cose che il gruppo non deve vedere: la tua scheda,
l'inventario, e quello che il Ladro scopre andando in avanscoperta.
Ora che mi hai scritto posso raggiungerti.

/scheda - la tua scheda
/zaino - cosa porti addosso"""


@router.message(CommandStart(), F.chat.type == "private")
async def start(message: Message, repo: Repo) -> None:
    await repo.mark_private_ok(message.from_user.id)
    partite = await repo.active_games_for_player(message.from_user.id)
    coda = ""
    if partite:
        char_id = await repo.char_id_for(partite[0].id, message.from_user.id)
        personaggio = partite[0].state.char(char_id) if char_id else None
        if personaggio is not None:
            coda = f"\n\nSpedizione in corso: interpreti {esc(personaggio.name)}."
    await message.answer(BENVENUTO + coda)


@router.message(Command("zaino"), F.chat.type == "private")
async def zaino(message: Message, repo: Repo) -> None:
    from ddos.engine import content as C

    partite = await repo.active_games_for_player(message.from_user.id)
    if not partite:
        await message.answer("Non sei in nessuna spedizione.")
        return
    riga = partite[0]
    char_id = await repo.char_id_for(riga.id, message.from_user.id)
    personaggio = riga.state.char(char_id) if char_id else None
    if personaggio is None:
        await message.answer("Non hai un personaggio in questa spedizione.")
        return
    if not personaggio.inventory:
        await message.answer("Zaino vuoto.")
        return
    righe = [f"{C.item(k).name} x{q}" for k, q in sorted(personaggio.inventory.items())]
    await message.answer(pre([f"ZAINO DI {personaggio.name.upper()}", ""] + righe))
