"""Le regole: tiri per colpire, danni, salvezze, morte, creazione personaggi.

Funzioni pure, salvo la mutazione esplicita delle entita' passate. Nessun I/O,
nessuna randomicita' implicita: il `Roller` arriva sempre da fuori.
"""

from __future__ import annotations

from ddos.engine import content as C
from ddos.engine.dice import Roller
from ddos.engine.entities import (
    ABILITY_ORDER,
    Ability,
    Character,
    ClassId,
    Combatant,
    Condition,
    Item,
    Monster,
    Status,
    ability_mod,
)
from ddos.engine.events import Event, ev

# --------------------------------------------------------------------------
# Statistiche derivate
# --------------------------------------------------------------------------


def armor_class(target: Combatant) -> int:
    """CA effettiva, condizioni incluse."""
    if isinstance(target, Monster):
        base = target.ac
    else:
        armor = C.ITEMS.get(target.armor)
        base = 10 + target.mod(Ability.DES) + (armor.ac_bonus if armor else 0)
    bonus = 0
    if target.has_condition(Condition.DIFESA):
        bonus += 2
    if target.has_condition(Condition.SCUDO):
        bonus += 4
    return base + bonus


def attack_bonus(ch: Character) -> int:
    """Mod. caratteristica + bonus di livello (il Guerriero ne prende uno in piu')."""
    cdef = C.CLASSES[ch.cls]
    ability = Ability.DES if ch.cls is ClassId.LADRO else Ability.FOR
    level_bonus = (ch.level + 1) // 2
    if cdef.id is ClassId.GUERRIERO:
        level_bonus += 1
    bonus = ch.mod(ability) + level_bonus
    if ch.has_condition(Condition.BENEDETTO):
        bonus += 1
    return bonus


def damage_bonus(ch: Character) -> int:
    if ch.cls is ClassId.LADRO:
        return max(0, ch.mod(Ability.DES))
    return ch.mod(Ability.FOR)


def save_bonus(entity: Combatant, ability: Ability) -> int:
    if isinstance(entity, Monster):
        # I mostri salvano con un bonus piatto legato alla loro pericolosita'.
        bonus = entity.attack_bonus // 2
    else:
        bonus = entity.mod(ability)
        if C.CLASSES[entity.cls].save_ability is ability:
            bonus += 2
    if entity.has_condition(Condition.BENEDETTO):
        bonus += 1
    return bonus


def spell_dc(ch: Character) -> int:
    """CD delle salvezze contro gli incantesimi del personaggio."""
    return 10 + ch.mod(C.CLASSES[ch.cls].primary) + ch.level // 2


def sneak_dice(ch: Character) -> str:
    """Dadi dell'attacco furtivo: 1d6 ogni due livelli."""
    return f"{max(1, (ch.level + 1) // 2)}d6"


def weapon_of(ch: Character) -> Item:
    return C.ITEMS.get(ch.weapon) or C.ITEMS["pugnale"]


def max_hp_for(cls: ClassId, level: int, con_mod: int, roller: Roller) -> int:
    """PF al livello dato: dado pieno al primo livello, poi tirati."""
    cdef = C.CLASSES[cls]
    total = cdef.hit_die + con_mod
    for _ in range(level - 1):
        total += max(1, roller.d(cdef.hit_die) + con_mod)
    return max(1, total)


# --------------------------------------------------------------------------
# Creazione del personaggio
# --------------------------------------------------------------------------


def roll_abilities(roller: Roller, cls: ClassId) -> dict[Ability, int]:
    """4d6 scarta il minore; il valore migliore va nella caratteristica di classe."""
    scores = sorted((roller.best_of(4, 6, 3)[0] for _ in ABILITY_ORDER), reverse=True)
    primary = C.CLASSES[cls].primary
    order = [primary] + [a for a in ABILITY_ORDER if a is not primary]
    return {ability: score for ability, score in zip(order, scores)}


def create_character(
    roller: Roller,
    *,
    char_id: str,
    name: str,
    cls: ClassId,
    telegram_id: int | None = None,
) -> Character:
    """Un personaggio di 1o livello con equipaggiamento di classe."""
    cdef = C.CLASSES[cls]
    abilities = roll_abilities(roller, cls)
    con_mod = ability_mod(abilities[Ability.COS])
    hp = max(1, cdef.hit_die + con_mod)
    ch = Character(
        id=char_id,
        name=name,
        cls=cls,
        abilities=abilities,
        max_hp=hp,
        hp=hp,
        weapon=cdef.weapon,
        armor=cdef.armor,
        spell_slots=cdef.spell_slots_per_level,
        spell_slots_max=cdef.spell_slots_per_level,
        telegram_id=telegram_id,
    )
    for key, qty in cdef.items:
        ch.add_item(key, qty)
    return ch


def grant_xp(ch: Character, amount: int, roller: Roller) -> list[Event]:
    """Assegna PX e applica eventuali passaggi di livello."""
    if amount <= 0 or ch.dead:
        return []
    ch.xp += amount
    events = [ev("px", f"{ch.name} guadagna {amount} PX.", char=ch.id, amount=amount)]
    target = C.level_for_xp(ch.xp)
    while ch.level < target:
        events.extend(level_up(ch, roller))
    return events


def level_up(ch: Character, roller: Roller) -> list[Event]:
    cdef = C.CLASSES[ch.cls]
    ch.level += 1
    gain = max(1, roller.d(cdef.hit_die) + ch.mod(Ability.COS))
    ch.max_hp += gain
    ch.hp += gain
    if cdef.spell_slots_per_level:
        ch.spell_slots_max += cdef.spell_slots_per_level
        ch.spell_slots = ch.spell_slots_max
    return [
        ev(
            "livello",
            f"{ch.name} sale al livello {ch.level}! (+{gain} PF max)",
            char=ch.id,
            level=ch.level,
            hp_gain=gain,
        )
    ]


# --------------------------------------------------------------------------
# Danno, cura, morte
# --------------------------------------------------------------------------


def apply_damage(target: Combatant, amount: int) -> list[Event]:
    """Applica danno gestendo agonia e morte. Non tira dadi."""
    amount = max(0, amount)
    if isinstance(target, Monster):
        target.hp -= amount
        events = [
            ev("danno", f"{target.display} subisce {amount} danni.",
               target=target.id, amount=amount, hp=max(0, target.hp))
        ]
        if target.hp <= 0:
            target.hp = 0
            events.append(ev("morte", f"{target.display} cade a terra, sconfitto.",
                             target=target.id))
        return events

    if target.status is Status.MORTO:
        return []

    if target.status is Status.MORENTE:
        # Colpire chi e' gia' a terra accelera la fine.
        target.death_failures = min(3, target.death_failures + 1)
        events = [ev("danno", f"{target.name}, gia' a terra, incassa un altro colpo.",
                     target=target.id, amount=amount)]
        if target.death_failures >= 3:
            target.status = Status.MORTO
            events.append(ev("morte", f"{target.name} e' morto.", target=target.id))
        return events

    target.hp -= amount
    events = [
        ev("danno", f"{target.name} subisce {amount} danni.",
           target=target.id, amount=amount, hp=max(0, target.hp))
    ]
    if target.hp <= 0:
        target.hp = 0
        target.status = Status.MORENTE
        target.death_successes = 0
        target.death_failures = 0
        target.conditions.clear()
        events.append(
            ev("morente", f"{target.name} crolla a terra, morente!", target=target.id)
        )
    return events


def heal(target: Combatant, amount: int) -> list[Event]:
    """Cura. Un personaggio morente torna in piedi con i PF curati."""
    amount = max(0, amount)
    if isinstance(target, Monster):
        target.hp = min(target.max_hp, target.hp + amount)
        return [ev("cura", f"{target.display} recupera {amount} PF.", target=target.id)]

    if target.status is Status.MORTO:
        return [ev("errore", f"{target.name} e' morto. La cura non basta piu'.",
                   target=target.id)]

    revived = target.status is Status.MORENTE
    if revived:
        target.status = Status.VIVO
        target.death_successes = 0
        target.death_failures = 0
        target.hp = 0
    target.hp = min(target.max_hp, target.hp + max(1, amount))
    if revived:
        return [
            ev("cura", f"{target.name} torna in piedi con {target.hp} PF!",
               target=target.id, amount=amount, revived=True)
        ]
    return [
        ev("cura", f"{target.name} recupera {amount} PF ({target.hp}/{target.max_hp}).",
           target=target.id, amount=amount)
    ]


def death_save(ch: Character, roller: Roller) -> list[Event]:
    """Tiro di stabilizzazione a inizio turno di un morente."""
    if ch.status is not Status.MORENTE:
        return []
    if ch.death_successes >= 3:
        return []  # stabilizzato: resta a terra ma non peggiora
    roll = roller.d20()
    if roll == 20:
        ch.status = Status.VIVO
        ch.hp = 1
        ch.death_successes = 0
        ch.death_failures = 0
        return [ev("cura", f"{ch.name} riapre gli occhi e si rialza con 1 PF!",
                   char=ch.id, roll=roll)]
    if roll >= 10:
        ch.death_successes += 1
        if ch.death_successes >= 3:
            return [ev("salvezza", f"{ch.name} si stabilizza ({roll}). Respira ancora.",
                       char=ch.id, roll=roll)]
        return [ev("salvezza",
                   f"{ch.name} resiste ({roll}): {ch.death_successes}/3 successi.",
                   char=ch.id, roll=roll)]
    # Un 1 naturale vale doppio, ma il contatore si ferma a 3: un '4/3'
    # sulla scheda sarebbe solo confusione.
    ch.death_failures = min(3, ch.death_failures + (2 if roll == 1 else 1))
    if ch.death_failures >= 3:
        ch.status = Status.MORTO
        return [ev("morte", f"{ch.name} smette di respirare. E' morto.",
                   char=ch.id, roll=roll)]
    return [ev("salvezza",
               f"{ch.name} peggiora ({roll}): {ch.death_failures}/3 fallimenti.",
               char=ch.id, roll=roll)]


def saving_throw(
    entity: Combatant, ability: Ability, dc: int, roller: Roller
) -> tuple[bool, int, list[Event]]:
    """Tiro salvezza. Ritorna (superato, totale, eventi)."""
    roll = roller.d20()
    total = roll + save_bonus(entity, ability)
    success = roll != 1 and (total >= dc or roll == 20)
    name = entity.display if isinstance(entity, Monster) else entity.name
    verb = "supera" if success else "fallisce"
    return success, total, [
        ev("salvezza", f"{name} {verb} la salvezza su {ability} ({total} vs CD {dc}).",
           target=entity.id, roll=roll, total=total, dc=dc, success=success)
    ]


# --------------------------------------------------------------------------
# Attacchi
# --------------------------------------------------------------------------


def resolve_attack(
    attacker: Combatant,
    target: Combatant,
    roller: Roller,
    *,
    power: bool = False,
) -> list[Event]:
    """Un attacco in mischia o a distanza. `power` = Colpo Poderoso del Guerriero."""
    att_name = attacker.display if isinstance(attacker, Monster) else attacker.name
    tgt_name = target.display if isinstance(target, Monster) else target.name

    if isinstance(attacker, Monster):
        bonus = attacker.attack_bonus
        damage_expr = attacker.damage
        dmg_bonus = 0
    else:
        bonus = attack_bonus(attacker)
        damage_expr = weapon_of(attacker).damage or "1d4"
        dmg_bonus = damage_bonus(attacker)

    if power:
        bonus -= 2

    ac = armor_class(target)
    roll = roller.d20()
    total = roll + bonus
    events: list[Event] = []

    if roll == 1:
        events.append(ev("mancato", f"{att_name} manca clamorosamente {tgt_name} (1!).",
                         attacker=attacker.id, target=target.id, roll=roll))
        _consume_hidden(attacker, events)
        return events

    critical = roll == 20
    if not critical and total < ac:
        events.append(ev("mancato", f"{att_name} manca {tgt_name} ({total} vs CA {ac}).",
                         attacker=attacker.id, target=target.id, roll=roll, total=total, ac=ac))
        _consume_hidden(attacker, events)
        return events

    # --- colpito ---
    result = roller.roll(damage_expr)
    dice_total = sum(result.dice) + result.bonus
    if critical:
        dice_total += sum(roller.roll(damage_expr).dice)  # i dadi raddoppiano, i mod no
    damage = dice_total + dmg_bonus

    extra: list[str] = []
    if power:
        bonus_roll = roller.roll_total("1d8")
        damage += bonus_roll
        extra.append(f"colpo poderoso +{bonus_roll}")
    if (
        isinstance(attacker, Character)
        and attacker.cls is ClassId.LADRO
        and attacker.has_condition(Condition.NASCOSTO)
    ):
        sneak = roller.roll_total(sneak_dice(attacker))
        damage += sneak
        extra.append(f"furtivo +{sneak}")

    suffix = f" ({', '.join(extra)})" if extra else ""
    if critical:
        events.append(ev("critico", f"CRITICO! {att_name} squarcia {tgt_name}{suffix}.",
                         attacker=attacker.id, target=target.id, roll=roll))
    else:
        events.append(ev("colpo", f"{att_name} colpisce {tgt_name} ({total} vs CA {ac}){suffix}.",
                         attacker=attacker.id, target=target.id, roll=roll, total=total, ac=ac))

    _consume_hidden(attacker, events)
    events.extend(apply_damage(target, max(1, damage)))

    if isinstance(attacker, Character) and isinstance(target, Monster) and target.dead:
        attacker.kills += 1
    return events


def _consume_hidden(attacker: Combatant, events: list[Event]) -> None:
    if attacker.has_condition(Condition.NASCOSTO):
        attacker.clear_condition(Condition.NASCOSTO)
        events.append(ev("condizione", "L'ombra si dissolve: non sei piu' nascosto.",
                         target=attacker.id, condition=str(Condition.NASCOSTO)))


# --------------------------------------------------------------------------
# Incantesimi e oggetti
# --------------------------------------------------------------------------


def cast_spell(
    caster: Character,
    spell: C.Spell,
    targets: list[Combatant],
    roller: Roller,
) -> list[Event]:
    """Risolve un incantesimo su bersagli gia' selezionati."""
    if caster.spell_slots <= 0:
        return [ev("errore", f"{caster.name} non ha piu' slot incantesimo.", char=caster.id)]
    caster.spell_slots -= 1
    events = [ev("incantesimo", f"{caster.name} lancia {spell.name}!",
                 char=caster.id, spell=spell.key)]

    if spell.only_trait:
        targets = [t for t in targets
                   if isinstance(t, Monster) and spell.only_trait in t.traits]
        if not targets:
            events.append(ev("info", "Nessun bersaglio valido: l'energia si disperde.",
                             spell=spell.key))
            return events

    dc = spell_dc(caster)
    for target in targets:
        if spell.effect == "danno":
            events.extend(apply_damage(target, roller.roll_total(spell.dice)))
        elif spell.effect == "cura":
            events.extend(heal(target, roller.roll_total(spell.dice)))
        elif spell.effect == "danno_salvezza":
            amount = roller.roll_total(spell.dice)
            success, _, save_events = saving_throw(target, spell.save, dc, roller)
            events.extend(save_events)
            events.extend(apply_damage(target, amount // 2 if success else amount))
        elif spell.effect == "condizione":
            if spell.save is not None:
                success, _, save_events = saving_throw(target, spell.save, dc, roller)
                events.extend(save_events)
                if success:
                    continue
            target.add_condition(spell.condition, spell.rounds)
            name = target.display if isinstance(target, Monster) else target.name
            events.append(ev("condizione", f"{name}: {spell.condition} ({spell.rounds} round).",
                             target=target.id, condition=spell.condition))
    return events


def use_item(user: Character, key: str, target: Character, roller: Roller) -> list[Event]:
    """Consuma un oggetto dall'inventario. Le pozioni si possono dare a un compagno."""
    definition = C.ITEMS.get(key)
    if definition is None or key not in user.inventory:
        return [ev("errore", "Non hai quell'oggetto.", char=user.id, item=key)]
    if definition.kind != "pozione":
        return [ev("errore", f"{definition.name} non si puo' usare cosi'.",
                   char=user.id, item=key)]
    user.remove_item(key)
    events = [ev("info", f"{user.name} usa {definition.name} su {target.name}.",
                 char=user.id, item=key, target=target.id)]
    if definition.heal:
        events.extend(heal(target, roller.roll_total(definition.heal)))
    if key == "antidoto":
        target.clear_condition(Condition.VELENO)
        events.append(ev("condizione", f"{target.name} non e' piu' avvelenato.",
                         target=target.id))
    return events


def equip(ch: Character, key: str) -> list[Event]:
    """Equipaggia arma o armatura presente nell'inventario."""
    definition = C.ITEMS.get(key)
    if definition is None or key not in ch.inventory:
        return [ev("errore", "Non hai quell'oggetto.", char=ch.id, item=key)]
    if not definition.usable_by(ch.cls):
        return [ev("errore", f"Un {C.CLASSES[ch.cls].name} non sa usare {definition.name}.",
                   char=ch.id, item=key)]
    if definition.kind == "arma":
        previous, ch.weapon = ch.weapon, key
    elif definition.kind == "armatura":
        previous, ch.armor = ch.armor, key
    else:
        return [ev("errore", f"{definition.name} non si equipaggia.", char=ch.id, item=key)]
    ch.remove_item(key)
    if previous:
        ch.add_item(previous)
    return [ev("info", f"{ch.name} equipaggia {definition.name}.", char=ch.id, item=key)]


# --------------------------------------------------------------------------
# Mostri
# --------------------------------------------------------------------------

_LABELS = "ABCDEFGH"


def spawn_monster(kind: str, roller: Roller, label: str = "A", suffix: str = "") -> Monster:
    mdef = C.BESTIARY[kind]
    hp = max(1, roller.roll_total(mdef.hp_dice))
    return Monster(
        id=f"{kind}-{suffix or label}",
        kind=kind,
        name=mdef.name,
        max_hp=hp,
        hp=hp,
        ac=mdef.ac,
        attack_bonus=mdef.attack_bonus,
        damage=mdef.damage,
        xp=mdef.xp,
        initiative_mod=mdef.initiative_mod,
        label=label,
        traits=mdef.traits,
    )


def spawn_group(kind: str, roller: Roller, count: int | None = None) -> list[Monster]:
    mdef = C.BESTIARY[kind]
    if count is None:
        count = roller.randint(*mdef.group)
    return [spawn_monster(kind, roller, _LABELS[i], suffix=str(i + 1)) for i in range(count)]


def choose_target(monster: Monster, party: list[Character], roller: Roller) -> Character | None:
    """IA dei mostri: chi ha provocato, altrimenti un bersaglio in piedi a caso."""
    standing = [c for c in party if c.alive]
    if not standing:
        return None
    taunters = [c for c in standing if c.has_condition(Condition.PROVOCATO)]
    if taunters:
        return roller.choice(taunters)
    # Leggera preferenza per i feriti: i mostri fiutano il sangue.
    wounded = [c for c in standing if c.hp <= c.max_hp // 3]
    if wounded and roller.chance(0.4):
        return roller.choice(wounded)
    return roller.choice(standing)
