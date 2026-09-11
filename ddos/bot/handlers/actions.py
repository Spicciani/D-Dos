"""Bottoni inline: l'interfaccia vera del gioco.

Ogni callback porta il numero di turno con cui era stata disegnata. Se nel
frattempo la scena e' andata avanti, il tap viene rifiutato con un avviso
effimero invece di applicare un'azione vecchia: e' quello che rende innocuo il
doppio tap e la tastiera rimasta aperta su un telefono.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from ddos.bot import keyboards as KB
from ddos.bot.handlers.commands import nome_in_sospeso
from ddos.bot.session import GameService
from ddos.engine import actions as A
from ddos.engine.actions import Action
from ddos.render.views import pre, sheet_view
from ddos.store.repo import Repo

log = logging.getLogger("ddos.bottoni")
router = Router(name="bottoni")


@router.callback_query(F.data.startswith("m:"))
async def menu(query: CallbackQuery, service: GameService, repo: Repo) -> None:
    """Sottomenu: cambia solo la tastiera, non lo stato."""
    try:
        _, seq, voce = KB.parse_callback(query.data or "")
    except ValueError:
        await query.answer("Bottone non valido.")
        return

    riga = await repo.active_game(query.message.chat.id)
    if riga is None:
        await query.answer("Nessuna spedizione in corso.")
        return
    if seq != riga.state.turn_seq:
        await query.answer("La scena e' andata avanti.")
        return

    if voce == "scheda":
        await _scheda_privata(query, riga, repo)
        return

    if voce == "classi":
        markup = KB.class_kb(riga.state.turn_seq)
    else:
        markup = KB.scene_kb(riga.state, menu_aperto="" if voce == "principale" else voce)

    try:
        await query.message.edit_reply_markup(reply_markup=markup)
    except TelegramBadRequest:
        pass
    await query.answer()


@router.callback_query(F.data.startswith("a:"))
async def azione(query: CallbackQuery, service: GameService, repo: Repo) -> None:
    try:
        _, seq, grezza = KB.parse_callback(query.data or "")
    except ValueError:
        await query.answer("Bottone non valido.")
        return

    action = Action.decode(grezza)
    chat_id = query.message.chat.id

    if action.kind == A.JOIN:
        ripiego = query.from_user.first_name or "Anonimo"
        action = Action(A.JOIN, target=nome_in_sospeso(chat_id, query.from_user.id, ripiego),
                        value=action.value)

    esito = await service.apply(
        chat_id, action,
        telegram_id=query.from_user.id,
        expected_seq=seq,
        username=query.from_user.username or query.from_user.first_name or "",
    )

    if not esito.ok:
        await query.answer(esito.avviso[:200], show_alert=len(esito.avviso) > 60)
        return

    # Un riscontro breve sul bottone: il racconto lungo sta gia' nella scena.
    titoli = [e.text for e in esito.eventi
              if e.kind in ("critico", "morte", "livello", "bottino", "trappola")]
    await query.answer(titoli[0][:200] if titoli else "")


async def _scheda_privata(query: CallbackQuery, riga, repo: Repo) -> None:
    char_id = await repo.char_id_for(riga.id, query.from_user.id)
    personaggio = riga.state.char(char_id) if char_id else None
    if personaggio is None:
        await query.answer("Non hai un personaggio in questa spedizione.", show_alert=True)
        return
    try:
        await query.bot.send_message(query.from_user.id, pre(sheet_view(personaggio)))
        await repo.mark_private_ok(query.from_user.id)
        await query.answer("Scheda mandata in privato.")
    except Exception:
        await query.answer("Aprimi una chat privata e fai /start, poi riprova.",
                           show_alert=True)
