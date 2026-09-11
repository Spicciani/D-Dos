"""Il riduttore: `reduce(state, action) -> (nuovo stato, eventi)`.

Regole della casa:
  * lo stato in ingresso non viene mai modificato (si lavora su una copia);
  * un'azione non valida non consuma dadi e non avanza il turno: torna lo stato
    originale e un solo evento di errore;
  * dopo un'azione in combattimento il motore risolve da solo i turni dei mostri
    e si ferma solo quando tocca di nuovo a un giocatore. Il client non deve
    sapere nulla dell'ordine di iniziativa.
"""

from __future__ import annotations

from ddos.engine import actions as A
from ddos.engine import content as C
from ddos.engine import rules as R
from ddos.engine.dice import Roller
from ddos.engine.dungeon import DIRECTION_NAMES, Level, RoomKind, generate_level
from ddos.engine.entities import Ability, Character, ClassId, Condition, Monster, Status
from ddos.engine.events import Event, ev
from ddos.engine.state import MAX_TIER, PARTY_MAX, Combat, GameState, Phase

#: Limite di sicurezza per il ciclo dei turni automatici.
_MAX_AUTO_TURNS = 200


class InvalidAction(Exception):
    """Azione rifiutata: nulla viene modificato."""


def reduce(state: GameState, action: A.Action) -> tuple[GameState, list[Event]]:
    """Applica un'azione. Unico punto d'ingresso del motore."""
    working = GameState.from_dict(state.to_dict())
    roller = working.roller()
    try:
        events = _dispatch(working, action, roller)
    except InvalidAction as exc:
        return state, [ev("errore", str(exc), action=action.kind)]
    working.sync(roller)
    working.turn_seq += 1
    return working, events


# --------------------------------------------------------------------------
# Smistamento
# --------------------------------------------------------------------------


def _dispatch(state: GameState, action: A.Action, roller: Roller) -> list[Event]:
    if state.over:
        raise InvalidAction("La partita e' finita.")

    if state.phase is Phase.LOBBY:
        if action.kind not in A.LOBBY_ACTIONS:
            raise InvalidAction("La partita non e' ancora cominciata.")
        return _lobby(state, action, roller)

    if state.phase is Phase.ESPLORAZIONE:
        if action.kind not in A.EXPLORATION_ACTIONS:
            raise InvalidAction("Non si puo' fare adesso.")
        return _explore(state, action, roller)

    if state.phase is Phase.COMBATTIMENTO:
        if action.kind not in A.COMBAT_ACTIONS and action.kind != A.TICK:
            raise InvalidAction("Sei in combattimento.")
        return _combat(state, action, roller)

    raise InvalidAction("Stato di gioco imprevisto.")


def _actor(state: GameState, action: A.Action) -> Character:
    ch = state.char(action.actor)
    if ch is None:
        raise InvalidAction("Personaggio non trovato.")
    if ch.dead:
        raise InvalidAction(f"{ch.name} e' morto.")
    if ch.status is Status.MORENTE:
        raise InvalidAction(f"{ch.name} e' a terra e non puo' agire.")
    return ch


# --------------------------------------------------------------------------
# Lobby
# --------------------------------------------------------------------------


def _lobby(state: GameState, action: A.Action, roller: Roller) -> list[Event]:
    if action.kind == A.JOIN:
        return _join(state, action, roller)

    if action.kind == A.LEAVE:
        ch = state.char(action.actor)
        if ch is None:
            raise InvalidAction("Non fai parte di questa spedizione.")
        state.party.remove(ch)
        if state.leader_id == ch.id:
            state.leader_id = state.party[0].id if state.party else ""
        return [ev("info", f"{ch.name} lascia la compagnia.", char=ch.id)]

    if action.kind == A.SET_LEADER:
        target = state.char(action.target)
        if target is None:
            raise InvalidAction("Personaggio non trovato.")
        state.leader_id = target.id
        return [ev("info", f"{target.name} guida la spedizione.", char=target.id)]

    if action.kind == A.BEGIN:
        if not state.party:
            raise InvalidAction("Serve almeno un avventuriero.")
        return _begin(state, roller)

    raise InvalidAction("Azione sconosciuta.")


def _join(state: GameState, action: A.Action, roller: Roller) -> list[Event]:
    if len(state.party) >= PARTY_MAX:
        raise InvalidAction(f"La compagnia e' al completo ({PARTY_MAX}).")
    try:
        cls = ClassId(action.value)
    except ValueError:
        raise InvalidAction(f"Classe sconosciuta: {action.value!r}") from None

    name = (action.target or "Anonimo").strip()[:16]
    if any(c.name.lower() == name.lower() for c in state.party):
        raise InvalidAction(f"C'e' gia' un {name} nella compagnia.")

    char_id = action.actor or f"pg{len(state.party) + 1}"
    if state.char(char_id):
        raise InvalidAction("Hai gia' un personaggio in questa partita.")

    ch = R.create_character(roller, char_id=char_id, name=name, cls=cls)
    state.party.append(ch)
    if not state.leader_id:
        state.leader_id = ch.id
    return [
        ev("info",
           f"{ch.name}, {C.CLASSES[cls].name} di livello 1, si unisce "
           f"({ch.hp} PF, CA {R.armor_class(ch)}).",
           char=ch.id, cls=str(cls))
    ]


def _begin(state: GameState, roller: Roller) -> list[Event]:
    state.tier = 1
    state.level = generate_level(roller, 1, boss_level=(MAX_TIER == 1))
    state.phase = Phase.ESPLORAZIONE
    events = [
        ev("scena", f"=== SOTTERRANEO, LIVELLO {state.tier} === (seme {state.seed})",
           tier=state.tier, seed=state.seed)
    ]
    events.extend(_enter_room(state, state.level.entry_id, roller, first=True))
    return events


# --------------------------------------------------------------------------
# Esplorazione
# --------------------------------------------------------------------------


def _explore(state: GameState, action: A.Action, roller: Roller) -> list[Event]:
    if action.kind == A.USE:
        return _use_item(state, action, roller)
    if action.kind == A.EQUIP:
        ch = _actor(state, action)
        return R.equip(ch, action.value)
    if action.kind == A.CAST:
        # Fuori dal combattimento si lancia solo cio' che non cerca un nemico:
        # curare i compagni tra uno scontro e l'altro e' meta' del gioco.
        return _cast(state, _actor(state, action), action, roller)

    ch = _actor(state, action)
    room = state.room
    if room is None:
        raise InvalidAction("Non siete in nessuna stanza.")

    if action.kind == A.MOVE:
        return _move(state, ch, action.value, roller)

    if action.kind == A.SEARCH:
        return _search(state, ch, roller)

    if action.kind == A.DISARM:
        return _disarm(state, ch, roller)

    if action.kind == A.SCOUT:
        return _scout(state, ch, action.value, roller)

    if action.kind == A.TAKE:
        return _take(state, ch)

    if action.kind == A.PRAY:
        return _pray(state, ch, roller)

    if action.kind == A.REST:
        return _rest(state, roller)

    if action.kind == A.DESCEND:
        return _descend(state, roller)

    raise InvalidAction("Azione sconosciuta.")


def _move(state: GameState, ch: Character, direction: str, roller: Roller) -> list[Event]:
    if len(state.standing_party) > 1 and ch.id != state.leader_id:
        leader = state.leader
        raise InvalidAction(
            f"Decide chi guida la spedizione ({leader.name if leader else '?'})."
        )
    room = state.room
    destination = room.exits.get(direction)
    if destination is None:
        raise InvalidAction(f"Non c'e' un passaggio a {DIRECTION_NAMES.get(direction, direction)}.")

    events = [ev("movimento",
                 f"La compagnia si muove verso {DIRECTION_NAMES[direction]}.",
                 direction=direction)]
    events.extend(_tick_dying(state, roller))
    if state.phase is Phase.SCONFITTA:
        return events
    state.prev_room_id = room.id
    events.extend(_enter_room(state, destination, roller))
    return events


def _enter_room(
    state: GameState, room_id: str, roller: Roller, *, first: bool = False
) -> list[Event]:
    state.room_id = room_id
    room = state.level.rooms[room_id]
    already = room.visited
    room.visited = True

    events = [ev("scena", f"{room.label}. {room.flavor}",
                 room=room.id, room_kind=str(room.kind), known=already)]

    if room.cleared:
        return events

    if room.kind in (RoomKind.MOSTRI, RoomKind.BOSS):
        events.extend(_start_combat(state, roller))
    elif room.kind == RoomKind.TRAPPOLA and not room.trap_found:
        events.extend(_spring_trap(state, room, roller))
    elif room.kind == RoomKind.TESORO:
        events.append(ev("bottino", "C'e' qualcosa da raccogliere qui.", room=room.id))
    elif room.kind == RoomKind.SANTUARIO:
        events.append(ev("info", "L'altare attende un'offerta o una preghiera.",
                         room=room.id))
    elif room.kind == RoomKind.SCALA:
        events.append(ev("info", "La scala scende al livello successivo.", room=room.id))
    return events


def _spring_trap(state: GameState, room, roller: Roller) -> list[Event]:
    trap = C.TRAPS[room.trap]
    area = trap.key in ("runa", "gas")
    victims = state.standing_party if area else [roller.choice(state.standing_party)]
    events = [ev("trappola", f"{trap.name}! {trap.description}", trap=trap.key)]

    for victim in victims:
        success, _, save_events = R.saving_throw(victim, trap.save, trap.dc, roller)
        events.extend(save_events)
        damage = roller.roll_total(trap.damage)
        if success:
            events.extend(R.apply_damage(victim, damage // 2))
        else:
            events.extend(R.apply_damage(victim, damage))
            if trap.condition:
                victim.add_condition(trap.condition, trap.rounds)
                events.append(ev("condizione", f"{victim.name}: {trap.condition}.",
                                 target=victim.id, condition=trap.condition))
    room.cleared = True
    room.trap_found = True
    events.extend(_check_wipe(state))
    return events


def _search(state: GameState, ch: Character, roller: Roller) -> list[Event]:
    room = state.room
    ability = Ability.DES if ch.cls is ClassId.LADRO else Ability.SAG
    roll = roller.d20() + ch.mod(ability)
    dc = 10 + state.tier
    events = [ev("info", f"{ch.name} perlustra la stanza ({roll} vs CD {dc}).",
                 char=ch.id, roll=roll)]

    if room.kind == RoomKind.TRAPPOLA and not room.cleared and not room.trap_found:
        if roll >= dc:
            room.trap_found = True
            events.append(ev("trappola",
                             f"Trovata: {C.TRAPS[room.trap].name}. Meglio disinnescarla.",
                             trap=room.trap, found=True))
        else:
            events.append(ev("info", "Niente di evidente. Forse."))
        return events

    if roll >= dc + 4 and not room.cleared:
        coins = roller.roll_total(f"{state.tier}d10+{state.tier * 3}")
        state.gold += coins
        events.append(ev("bottino", f"Monete nascoste in una fessura: +{coins} oro.",
                         gold=coins))
    else:
        events.append(ev("info", "Nulla di interessante."))
    return events


def _disarm(state: GameState, ch: Character, roller: Roller) -> list[Event]:
    if ch.cls is not ClassId.LADRO:
        raise InvalidAction("Solo un Ladro sa disinnescare una trappola.")
    room = state.room
    if room.kind != RoomKind.TRAPPOLA or room.cleared:
        raise InvalidAction("Non c'e' nessuna trappola da disinnescare.")
    if not room.trap_found:
        raise InvalidAction("Prima bisogna trovarla: cerca nella stanza.")

    trap = C.TRAPS[room.trap]
    roll = roller.d20() + ch.mod(Ability.DES) + ch.level
    events = [ev("info", f"{ch.name} lavora sul meccanismo ({roll} vs CD {trap.dc}).",
                 char=ch.id, roll=roll)]
    if roll >= trap.dc:
        room.cleared = True
        events.append(ev("info", f"{trap.name} disinnescata. Nessuno si e' fatto male.",
                         trap=trap.key))
        events.extend(R.grant_xp(ch, 15 * state.tier, roller))
    elif roll <= trap.dc - 5:
        events.append(ev("trappola", "Uno scatto secco. Troppo tardi."))
        events.extend(_spring_trap(state, room, roller))
    else:
        events.append(ev("info", "Il meccanismo resiste. Si puo' riprovare."))
    return events


def _scout(state: GameState, ch: Character, direction: str, roller: Roller) -> list[Event]:
    if ch.cls is not ClassId.LADRO:
        raise InvalidAction("Solo un Ladro sa muoversi in avanscoperta.")
    destination = state.room.exits.get(direction)
    if destination is None:
        raise InvalidAction(f"Non c'e' un passaggio a {DIRECTION_NAMES.get(direction, direction)}.")

    target = state.level.rooms[destination]
    roll = roller.d20() + ch.mod(Ability.DES)
    dc = 8 + state.tier
    if roll < dc:
        return [ev("info", f"{ch.name} torna indietro senza aver visto nulla ({roll}).",
                   char=ch.id)]

    if target.kind in (RoomKind.MOSTRI, RoomKind.BOSS):
        mdef = C.BESTIARY[target.monster_kind]
        detail = f"{target.monster_count}x {mdef.name} (CA {mdef.ac})"
    elif target.kind == RoomKind.TRAPPOLA:
        detail = f"una trappola: {C.TRAPS[target.trap].name}"
        target.trap_found = True
    elif target.kind == RoomKind.TESORO:
        detail = "un bottino incustodito"
    else:
        detail = target.label.lower()

    return [
        ev("info", f"{ch.name} sguscia nell'ombra e torna con una dritta.", char=ch.id),
        ev("sussurro", f"A {DIRECTION_NAMES[direction]}: {detail}.",
           to=ch.id, room=target.id, direction=direction),
    ]


def _take(state: GameState, ch: Character) -> list[Event]:
    room = state.room
    if room.cleared or (not room.loot and not room.gold):
        raise InvalidAction("Qui non c'e' piu' nulla.")

    events: list[Event] = []
    for key in room.loot:
        ch.add_item(key)
        events.append(ev("bottino", f"{ch.name} raccoglie {C.item(key).name}.",
                         char=ch.id, item=key))
    if room.gold:
        state.gold += room.gold
        events.append(ev("bottino", f"+{room.gold} monete d'oro nelle tasche comuni.",
                         gold=room.gold))
    room.loot = []
    room.gold = 0
    room.cleared = True
    return events


def _pray(state: GameState, ch: Character, roller: Roller) -> list[Event]:
    room = state.room
    if room.kind != RoomKind.SANTUARIO or room.cleared:
        raise InvalidAction("Non c'e' nessun santuario attivo qui.")
    room.cleared = True

    key, line = roller.choice(C.SHRINE_BLESSINGS)
    events = [ev("info", f"{ch.name} si inginocchia davanti all'altare. {line}", char=ch.id)]
    if key == "cura":
        for member in state.living_party:
            events.extend(R.heal(member, roller.roll_total("2d4+2")))
    elif key == "benedizione":
        for member in state.standing_party:
            member.add_condition(Condition.BENEDETTO, 5)
        events.append(ev("condizione", "Tutto il party e' benedetto (5 round).",
                         condition=str(Condition.BENEDETTO)))
    else:
        for member in state.standing_party:
            member.spell_slots = member.spell_slots_max
        events.append(ev("info", "Gli slot incantesimo sono ripristinati."))
    return events


def _rest(state: GameState, roller: Roller) -> list[Event]:
    events = [ev("info", "La compagnia si ferma a riprendere fiato.")]
    events.extend(_tick_dying(state, roller))
    if state.phase is Phase.SCONFITTA:
        return events

    events.extend(_revive_stable(state))
    for member in state.standing_party:
        events.extend(R.heal(member, roller.roll_total(f"1d4+{member.level}")))

    # Riposare nel sotterraneo attira compagnia: e' il prezzo delle cure gratis.
    if roller.chance(0.35):
        room = state.room
        pool = [m.kind for m in C.monsters_for_tier(state.tier)]
        room.monster_kind = roller.choice(pool)
        low, high = C.BESTIARY[room.monster_kind].group
        room.monster_count = max(1, roller.randint(low, high) - 1)
        room.cleared = False
        events.append(ev("scena", "Passi nel corridoio. Il riposo e' finito."))
        events.extend(_start_combat(state, roller))
    return events


def _descend(state: GameState, roller: Roller) -> list[Event]:
    room = state.room
    if room.kind != RoomKind.SCALA:
        raise InvalidAction("Non c'e' nessuna scala qui.")
    if state.tier >= MAX_TIER:
        raise InvalidAction("Piu' in basso non si va.")

    state.tier += 1
    state.level = generate_level(roller, state.tier, boss_level=(state.tier >= MAX_TIER))
    state.prev_room_id = ""
    events = [
        ev("scena", f"=== SOTTERRANEO, LIVELLO {state.tier} ===", tier=state.tier)
    ]
    # Scendere e' il vero momento di respiro: e' la ricompensa per aver ripulito
    # il livello, ed e' l'unica cosa che tiene in piedi un party senza chierico.
    events.extend(_revive_stable(state))
    for member in state.standing_party:
        member.spell_slots = member.spell_slots_max
        events.extend(R.heal(member, roller.roll_total(f"1d8+{member.level}")))
    events.extend(_enter_room(state, state.level.entry_id, roller))
    return events


def _use_item(state: GameState, action: A.Action, roller: Roller) -> list[Event]:
    ch = _actor(state, action)
    target = state.char(action.target) if action.target else ch
    if target is None:
        raise InvalidAction("Bersaglio non trovato.")
    events = R.use_item(ch, action.value, target, roller)
    if state.phase is Phase.COMBATTIMENTO:
        events.extend(_end_turn(state, roller))
    return events


def _revive_stable(state: GameState) -> list[Event]:
    """Chi si e' stabilizzato torna cosciente con 1 PF. Malconcio, ma in piedi."""
    events: list[Event] = []
    for member in state.party:
        if member.status is Status.MORENTE and member.death_successes >= 3:
            member.status = Status.VIVO
            member.hp = 1
            member.death_successes = 0
            member.death_failures = 0
            events.append(ev("cura", f"{member.name} riprende conoscenza (1 PF).",
                             char=member.id))
    return events


def _tick_dying(state: GameState, roller: Roller) -> list[Event]:
    """Il tempo passa anche per chi e' a terra."""
    events: list[Event] = []
    for member in state.party:
        if member.status is Status.MORENTE:
            events.extend(R.death_save(member, roller))
    events.extend(_check_wipe(state))
    return events


def _check_wipe(state: GameState) -> list[Event]:
    if state.standing_party:
        return []
    if any(c.status is Status.MORENTE for c in state.party):
        state.phase = Phase.SCONFITTA
        return [ev("fine", "Nessuno e' piu' in piedi. Il buio si richiude sulla compagnia.")]
    state.phase = Phase.SCONFITTA
    return [ev("fine", "L'intera compagnia e' caduta. Il sotterraneo vi tiene.")]


# --------------------------------------------------------------------------
# Combattimento
# --------------------------------------------------------------------------


def _start_combat(state: GameState, roller: Roller) -> list[Event]:
    room = state.room
    monsters = R.spawn_group(room.monster_kind, roller, room.monster_count or None)
    combat = Combat(monsters=monsters)

    for member in state.standing_party:
        combat.initiative[member.id] = roller.d20() + member.mod(Ability.DES)
    for monster in monsters:
        combat.initiative[monster.id] = roller.d20() + monster.initiative_mod

    combat.order = sorted(
        combat.initiative,
        key=lambda eid: (-combat.initiative[eid], eid),
    )
    combat.index = 0
    combat.round = 1
    state.combat = combat
    state.phase = Phase.COMBATTIMENTO

    names = ", ".join(m.display for m in monsters)
    events = [ev("scena", f"COMBATTIMENTO! {names}", monsters=[m.id for m in monsters])]
    events.extend(_resolve_until_player(state, roller))
    return events


def _combat(state: GameState, action: A.Action, roller: Roller) -> list[Event]:
    combat = state.combat
    if combat is None:
        raise InvalidAction("Non c'e' nessun combattimento in corso.")

    if action.kind == A.TICK:
        current = state.char(combat.current_id)
        if current is None:
            raise InvalidAction("Non tocca a un giocatore.")
        events = [ev("turno", f"{current.name} esita troppo a lungo: si mette in guardia.",
                     char=current.id)]
        current.add_condition(Condition.DIFESA, 1)
        events.extend(_end_turn(state, roller))
        return events

    ch = _actor(state, action)
    if ch.id != combat.current_id:
        current = state.combatant(combat.current_id)
        name = getattr(current, "name", "qualcun altro")
        raise InvalidAction(f"Non e' il tuo turno: tocca a {name}.")

    if action.kind == A.USE:
        return _use_item(state, action, roller)

    if action.kind in (A.ATTACK, A.POWER):
        target = state.monster(action.target)
        if target is None or not target.alive:
            raise InvalidAction("Bersaglio non valido.")
        if action.kind == A.POWER and ch.cls is not ClassId.GUERRIERO:
            raise InvalidAction("Solo un Guerriero puo' caricare un Colpo Poderoso.")
        events = R.resolve_attack(ch, target, roller, power=(action.kind == A.POWER))
        # Il Guerriero di 5o livello mena due volte.
        if ch.cls is ClassId.GUERRIERO and ch.level >= 5 and target.alive:
            events.extend(R.resolve_attack(ch, target, roller))
        events.extend(_end_turn(state, roller))
        return events

    if action.kind == A.DEFEND:
        ch.add_condition(Condition.DIFESA, 1)
        events = [ev("condizione", f"{ch.name} alza la guardia (+2 CA).", char=ch.id)]
        events.extend(_end_turn(state, roller))
        return events

    if action.kind == A.TAUNT:
        if ch.cls is not ClassId.GUERRIERO:
            raise InvalidAction("Solo un Guerriero sa attirare l'attenzione cosi'.")
        ch.add_condition(Condition.PROVOCATO, 2)
        ch.add_condition(Condition.DIFESA, 1)
        events = [ev("condizione",
                     f"{ch.name} urla e batte lo scudo: i nemici guardano solo lui.",
                     char=ch.id)]
        events.extend(_end_turn(state, roller))
        return events

    if action.kind == A.HIDE:
        if ch.cls is not ClassId.LADRO:
            raise InvalidAction("Solo un Ladro sa sparire in mezzo a una rissa.")
        roll = roller.d20() + ch.mod(Ability.DES)
        dc = 12
        if roll >= dc:
            ch.add_condition(Condition.NASCOSTO, 2)
            events = [ev("condizione", f"{ch.name} sparisce nell'ombra ({roll}).", char=ch.id)]
        else:
            events = [ev("info", f"{ch.name} non trova un angolo buio ({roll} vs {dc}).",
                         char=ch.id)]
        events.extend(_end_turn(state, roller))
        return events

    if action.kind == A.CAST:
        return _cast(state, ch, action, roller)

    if action.kind == A.FLEE:
        return _flee(state, ch, roller)

    raise InvalidAction("Azione sconosciuta.")


def _cast(state: GameState, ch: Character, action: A.Action, roller: Roller) -> list[Event]:
    spell = C.SPELLS.get(action.value)
    if spell is None:
        raise InvalidAction("Incantesimo sconosciuto.")
    if spell.cls is not ch.cls:
        raise InvalidAction(f"{spell.name} non e' un incantesimo da {C.CLASSES[ch.cls].name}.")
    if ch.level < spell.min_level:
        raise InvalidAction(f"{spell.name} si impara al livello {spell.min_level}.")
    if ch.spell_slots <= 0:
        raise InvalidAction("Non hai piu' slot incantesimo.")

    hostile = spell.target in (C.TARGET_NEMICO, C.TARGET_TUTTI_NEMICI)
    if hostile and state.phase is not Phase.COMBATTIMENTO:
        raise InvalidAction(f"{spell.name} ha bisogno di un nemico davanti.")

    if spell.target == C.TARGET_NEMICO:
        target = state.monster(action.target)
        if target is None or not target.alive:
            raise InvalidAction("Bersaglio non valido.")
        targets = [target]
    elif spell.target == C.TARGET_ALLEATO:
        ally = state.char(action.target)
        if ally is None or ally.dead:
            raise InvalidAction("Bersaglio non valido.")
        targets = [ally]
    elif spell.target == C.TARGET_SE_STESSO:
        targets = [ch]
    elif spell.target == C.TARGET_TUTTI_NEMICI:
        targets = list(state.live_monsters)
    else:
        targets = list(state.living_party)

    events = R.cast_spell(ch, spell, targets, roller)
    if state.phase is Phase.COMBATTIMENTO:
        events.extend(_end_turn(state, roller))
    return events


def _flee(state: GameState, ch: Character, roller: Roller) -> list[Event]:
    if not state.prev_room_id:
        raise InvalidAction("Non c'e' nessuna via di ritirata.")
    dc = 10 + state.tier
    roll = roller.d20() + ch.mod(Ability.DES)
    if roll < dc:
        events = [ev("info", f"{ch.name} prova a svincolarsi e non ci riesce ({roll} vs {dc}).",
                     char=ch.id)]
        events.extend(_end_turn(state, roller))
        return events

    events = [ev("movimento",
                 f"{ch.name} copre la ritirata: la compagnia arretra!", char=ch.id)]
    state.combat = None
    state.phase = Phase.ESPLORAZIONE
    for member in state.party:
        member.clear_condition(Condition.NASCOSTO)
        member.clear_condition(Condition.PROVOCATO)
    destination = state.prev_room_id
    state.prev_room_id = ""
    events.extend(_enter_room(state, destination, roller))
    return events


# --------------------------------------------------------------------------
# Motore dei turni
# --------------------------------------------------------------------------


def _end_turn(state: GameState, roller: Roller) -> list[Event]:
    events = _check_combat_end(state, roller)
    if events or state.phase is not Phase.COMBATTIMENTO:
        return events
    _advance_index(state)
    return _resolve_until_player(state, roller)


def _advance_index(state: GameState) -> None:
    combat = state.combat
    combat.index += 1
    if combat.index >= len(combat.order):
        combat.index = 0
        combat.round += 1


def _resolve_until_player(state: GameState, roller: Roller) -> list[Event]:
    """Fa giocare i mostri e salta chi non puo' agire, poi passa la mano."""
    events: list[Event] = []
    for _ in range(_MAX_AUTO_TURNS):
        ended = _check_combat_end(state, roller)
        if ended or state.phase is not Phase.COMBATTIMENTO:
            events.extend(ended)
            return events

        combat = state.combat
        entity = state.combatant(combat.current_id)

        if entity is None or entity.down:
            _advance_index(state)
            continue

        start_events, skip = _begin_turn(state, entity, roller)
        events.extend(start_events)

        if entity.down or skip:
            ended = _check_combat_end(state, roller)
            if ended or state.phase is not Phase.COMBATTIMENTO:
                events.extend(ended)
                return events
            _advance_index(state)
            continue

        if isinstance(entity, Monster):
            events.extend(_monster_turn(state, entity, roller))
            _advance_index(state)
            continue

        # Tocca a un giocatore: il motore si ferma e aspetta.
        events.append(ev("turno", f"Tocca a {entity.name}.",
                         char=entity.id, round=combat.round))
        return events

    events.append(ev("errore", "Il combattimento si e' incartato: interrotto."))
    state.phase = Phase.ESPLORAZIONE
    state.combat = None
    return events


def _begin_turn(state: GameState, entity, roller: Roller) -> tuple[list[Event], bool]:
    """Condizioni a inizio turno. Ritorna (eventi, salta_il_turno)."""
    events: list[Event] = []
    skip = False

    if isinstance(entity, Character) and entity.status is Status.MORENTE:
        return R.death_save(entity, roller), True

    if entity.has_condition(Condition.STORDITO):
        name = entity.display if isinstance(entity, Monster) else entity.name
        events.append(ev("condizione", f"{name} e' stordito e perde il turno.",
                         target=entity.id))
        skip = True

    if entity.has_condition(Condition.VELENO):
        damage = roller.roll_total("1d4")
        name = entity.display if isinstance(entity, Monster) else entity.name
        events.append(ev("danno", f"Il veleno morde {name}.", target=entity.id))
        events.extend(R.apply_damage(entity, damage))

    for key in list(entity.conditions):
        entity.conditions[key] -= 1
        if entity.conditions[key] <= 0:
            del entity.conditions[key]

    return events, skip


def _monster_turn(state: GameState, monster: Monster, roller: Roller) -> list[Event]:
    target = R.choose_target(monster, state.party, roller)
    if target is None:
        return []
    events = R.resolve_attack(monster, target, roller)
    # I boss agiscono due volte per round: e' quello che li rende boss.
    if "boss" in monster.traits and state.standing_party:
        second = R.choose_target(monster, state.party, roller)
        if second is not None:
            events.extend(R.resolve_attack(monster, second, roller))
    if "veleno" in monster.traits and isinstance(target, Character):
        if target.alive and roller.chance(0.3):
            target.add_condition(Condition.VELENO, 3)
            events.append(ev("condizione", f"{target.name} e' avvelenato!",
                             target=target.id))
    return events


def _check_combat_end(state: GameState, roller: Roller) -> list[Event]:
    combat = state.combat
    if combat is None or state.phase is not Phase.COMBATTIMENTO:
        return []

    if not state.standing_party:
        state.combat = None
        return _check_wipe(state)

    if state.live_monsters:
        return []

    # --- vittoria nello scontro ---
    room = state.room
    xp_total = sum(C.BESTIARY[m.kind].xp for m in combat.monsters)
    survivors = state.standing_party
    share = max(1, xp_total // max(1, len(survivors)))

    events = [ev("scena", "La stanza e' sgombra.", room=room.id if room else "")]
    for member in survivors:
        member.clear_condition(Condition.NASCOSTO)
        member.clear_condition(Condition.PROVOCATO)
        events.extend(R.grant_xp(member, share, roller))

    was_boss = room is not None and room.kind == RoomKind.BOSS
    state.combat = None
    state.phase = Phase.ESPLORAZIONE
    if room is not None:
        room.cleared = not (room.loot or room.gold)
        if room.loot or room.gold:
            events.append(ev("bottino", "I nemici custodivano qualcosa.", room=room.id))

    if was_boss and state.tier >= MAX_TIER:
        state.phase = Phase.VITTORIA
        events.append(ev("fine",
                         "Il Signore delle Ossa si sgretola. Il sotterraneo e' vostro."))
    return events
