"""Cataloghi: classi, oggetti, incantesimi, bestiario, tesori.

Solo dati. Le regole che li usano stanno in `rules.py`. Ogni classe deve avere
almeno una cosa che sa fare *solo lei*: e' quello che rende il party un party.
"""

from __future__ import annotations

from dataclasses import dataclass

from ddos.engine.entities import Ability, ClassId, Condition, Item

# --------------------------------------------------------------------------
# Progressione
# --------------------------------------------------------------------------

#: PX necessari per raggiungere il livello (indice = livello - 1).
#: Tarati sul bottino reale di un sotterraneo da 3 livelli, PX divisi per 4:
#: chi ripulisce le stanze arriva dal boss al 4o livello, chi corre al 3o.
XP_THRESHOLDS: tuple[int, ...] = (0, 60, 160, 320, 550)
MAX_LEVEL = len(XP_THRESHOLDS)


def level_for_xp(xp: int) -> int:
    level = 1
    for i, threshold in enumerate(XP_THRESHOLDS, start=1):
        if xp >= threshold:
            level = i
    return level


def xp_to_next(xp: int) -> int | None:
    """PX mancanti al livello successivo, None se al massimo."""
    level = level_for_xp(xp)
    if level >= MAX_LEVEL:
        return None
    return XP_THRESHOLDS[level] - xp


# --------------------------------------------------------------------------
# Incantesimi e talenti
# --------------------------------------------------------------------------

TARGET_NEMICO = "nemico"
TARGET_ALLEATO = "alleato"
TARGET_SE_STESSO = "se_stesso"
TARGET_TUTTI_NEMICI = "tutti_nemici"
TARGET_TUTTI_ALLEATI = "tutti_alleati"


@dataclass(frozen=True, slots=True)
class Spell:
    key: str
    name: str
    cls: ClassId
    target: str
    effect: str            # danno | cura | condizione | danno_salvezza
    dice: str = ""
    condition: str = ""
    rounds: int = 0
    save: Ability | None = None
    save_dc_base: int = 10
    only_trait: str = ""   # colpisce solo i mostri con questo tratto
    min_level: int = 1
    description: str = ""


SPELLS: dict[str, Spell] = {
    "dardo": Spell(
        key="dardo",
        name="Dardo Incantato",
        cls=ClassId.MAGO,
        target=TARGET_NEMICO,
        effect="danno",
        dice="1d4+2",
        description="Un dardo di forza pura. Non manca mai il bersaglio.",
    ),
    "sonno": Spell(
        key="sonno",
        name="Sonno",
        cls=ClassId.MAGO,
        target=TARGET_NEMICO,
        effect="condizione",
        condition=str(Condition.STORDITO),
        rounds=2,
        save=Ability.SAG,
        description="Il bersaglio crolla addormentato se fallisce la salvezza.",
    ),
    "scudo_arcano": Spell(
        key="scudo_arcano",
        name="Scudo Arcano",
        cls=ClassId.MAGO,
        target=TARGET_SE_STESSO,
        effect="condizione",
        condition=str(Condition.SCUDO),
        rounds=3,
        description="Una barriera invisibile: +4 alla Classe Armatura.",
    ),
    "dardi_multipli": Spell(
        key="dardi_multipli",
        name="Sciame di Dardi",
        cls=ClassId.MAGO,
        target=TARGET_TUTTI_NEMICI,
        effect="danno",
        dice="1d4",
        min_level=3,
        description="Un dardo per ogni nemico nella stanza.",
    ),
    "cura": Spell(
        key="cura",
        name="Cura Ferite",
        cls=ClassId.CHIERICO,
        target=TARGET_ALLEATO,
        effect="cura",
        dice="1d8+2",
        description="Richiude le ferite. Riporta in piedi un compagno morente.",
    ),
    "benedizione": Spell(
        key="benedizione",
        name="Benedizione",
        cls=ClassId.CHIERICO,
        target=TARGET_TUTTI_ALLEATI,
        effect="condizione",
        condition=str(Condition.BENEDETTO),
        rounds=3,
        description="+1 ai tiri per colpire e alle salvezze di tutto il party.",
    ),
    "scacciare": Spell(
        key="scacciare",
        name="Scacciare Non-morti",
        cls=ClassId.CHIERICO,
        target=TARGET_TUTTI_NEMICI,
        effect="danno_salvezza",
        dice="2d6",
        save=Ability.SAG,
        only_trait="non-morto",
        description="Luce sacra: incenerisce i non-morti che non resistono.",
    ),
}


def spells_for(cls: ClassId, level: int) -> list[Spell]:
    return [s for s in SPELLS.values() if s.cls is cls and s.min_level <= level]


# --------------------------------------------------------------------------
# Oggetti
# --------------------------------------------------------------------------

ITEMS: dict[str, Item] = {
    # armi
    "pugnale": Item("pugnale", "Pugnale", "arma", damage="1d4", value=2),
    "spada_corta": Item("spada_corta", "Spada Corta", "arma", damage="1d6", value=10),
    "spada_lunga": Item("spada_lunga", "Spada Lunga", "arma", damage="1d8", value=15),
    "ascia_bipenne": Item(
        "ascia_bipenne", "Ascia Bipenne", "arma", damage="1d10", value=20, two_handed=True,
        classes=(ClassId.GUERRIERO,),
    ),
    "mazza": Item("mazza", "Mazza", "arma", damage="1d6", value=8),
    "bastone": Item("bastone", "Bastone", "arma", damage="1d6", value=5),
    "arco_corto": Item("arco_corto", "Arco Corto", "arma", damage="1d6", value=25),
    "lama_runica": Item(
        "lama_runica", "Lama Runica", "arma", damage="1d8+2", value=120,
        description="Incisa con rune che brillano vicino ai non-morti.",
    ),
    # armature
    "veste": Item("veste", "Veste da Mago", "armatura", ac_bonus=0, value=1),
    "cuoio": Item("cuoio", "Armatura di Cuoio", "armatura", ac_bonus=2, value=20),
    "cotta": Item("cotta", "Cotta di Maglia", "armatura", ac_bonus=4, value=60),
    "cotta_scudo": Item(
        "cotta_scudo", "Cotta e Scudo", "armatura", ac_bonus=5, value=80,
    ),
    "usbergo_nano": Item(
        "usbergo_nano", "Usbergo Nanico", "armatura", ac_bonus=6, value=250,
        description="Forgiato sotto la montagna. Non si scalfisce.",
    ),
    # consumabili
    "pozione_cura": Item(
        "pozione_cura", "Pozione di Cura", "pozione", heal="2d4+2", value=25,
    ),
    "pozione_grande": Item(
        "pozione_grande", "Pozione Maggiore", "pozione", heal="4d4+4", value=80,
    ),
    "antidoto": Item("antidoto", "Antidoto", "pozione", value=15,
                     description="Neutralizza il veleno."),
    # tesori (valgono solo oro e gloria)
    "moneta_antica": Item("moneta_antica", "Moneta Antica", "tesoro", value=10),
    "gemma": Item("gemma", "Gemma di Ossidiana", "tesoro", value=50),
    "idolo": Item("idolo", "Idolo d'Avorio", "tesoro", value=120),
    "corona": Item("corona", "Corona Spezzata", "tesoro", value=300),
    "chiave_ossa": Item("chiave_ossa", "Chiave d'Ossa", "chiave", value=0,
                        description="Apre la porta sigillata dell'ultimo livello."),
}


def item(key: str) -> Item:
    """Definizione dell'oggetto. Errore esplicito se il catalogo non lo ha."""
    try:
        return ITEMS[key]
    except KeyError:
        raise KeyError(f"oggetto sconosciuto: {key!r}") from None


# --------------------------------------------------------------------------
# Classi
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClassDef:
    id: ClassId
    name: str
    hit_die: int
    primary: Ability
    save_ability: Ability
    weapon: str
    armor: str
    items: tuple[tuple[str, int], ...]
    spell_slots_per_level: int
    talents: tuple[str, ...]
    blurb: str


CLASSES: dict[ClassId, ClassDef] = {
    ClassId.GUERRIERO: ClassDef(
        id=ClassId.GUERRIERO,
        name="Guerriero",
        hit_die=10,
        primary=Ability.FOR,
        save_ability=Ability.COS,
        weapon="spada_lunga",
        armor="cotta",
        items=(("pozione_cura", 1),),
        spell_slots_per_level=0,
        talents=("colpo_poderoso", "provocare"),
        blurb="Regge la linea. Piu' HP di chiunque, e li usa tutti.",
    ),
    ClassId.LADRO: ClassDef(
        id=ClassId.LADRO,
        name="Ladro",
        hit_die=6,
        primary=Ability.DES,
        save_ability=Ability.DES,
        weapon="spada_corta",
        armor="cuoio",
        items=(("pugnale", 1), ("pozione_cura", 1)),
        spell_slots_per_level=0,
        talents=("nascondersi", "avanscoperta", "disinnescare"),
        blurb="Vede le trappole prima di pestarle e colpisce dove fa male.",
    ),
    ClassId.MAGO: ClassDef(
        id=ClassId.MAGO,
        name="Mago",
        hit_die=4,
        primary=Ability.INT,
        save_ability=Ability.INT,
        weapon="bastone",
        armor="veste",
        items=(("pugnale", 1),),
        spell_slots_per_level=2,
        talents=(),
        blurb="Fragile come un guscio, decisivo come una bomba.",
    ),
    ClassId.CHIERICO: ClassDef(
        id=ClassId.CHIERICO,
        name="Chierico",
        hit_die=8,
        primary=Ability.SAG,
        save_ability=Ability.SAG,
        weapon="mazza",
        armor="cotta_scudo",
        items=(("pozione_cura", 1),),
        spell_slots_per_level=2,
        talents=(),
        blurb="L'unico che puo' rialzare chi e' a terra. Trattatelo bene.",
    ),
}


TALENTS: dict[str, str] = {
    "colpo_poderoso": "Colpo Poderoso - -2 a colpire, +1d8 ai danni",
    "provocare": "Provocare - i nemici si concentrano su di te per 1 round",
    "nascondersi": "Nascondersi - prova di DES, il prossimo attacco e' furtivo",
    "avanscoperta": "Avanscoperta - sbircia la stanza accanto, in privato",
    "disinnescare": "Disinnescare - neutralizza una trappola prima che scatti",
}


# --------------------------------------------------------------------------
# Bestiario
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MonsterDef:
    kind: str
    name: str
    hp_dice: str
    ac: int
    attack_bonus: int
    damage: str
    xp: int
    initiative_mod: int = 0
    traits: tuple[str, ...] = ()
    group: tuple[int, int] = (1, 1)   # quanti ne compaiono insieme
    tiers: tuple[int, ...] = (1,)     # a quali profondita' puo' apparire
    glyph: str = "g"
    boss_of: int = 0                  # >0 se e' il boss di quel livello


BESTIARY: dict[str, MonsterDef] = {
    "ratto": MonsterDef(
        "ratto", "Ratto Gigante", "1d4+1", ac=11, attack_bonus=1, damage="1d3",
        xp=10, initiative_mod=2, group=(2, 4), tiers=(1,), glyph="r",
    ),
    "goblin": MonsterDef(
        "goblin", "Goblin", "1d6+2", ac=12, attack_bonus=2, damage="1d6",
        xp=20, initiative_mod=1, group=(2, 3), tiers=(1, 2), glyph="g",
    ),
    "scheletro": MonsterDef(
        "scheletro", "Scheletro", "1d8+2", ac=13, attack_bonus=2, damage="1d6",
        xp=25, traits=("non-morto",), group=(1, 3), tiers=(1, 2), glyph="s",
    ),
    "melma": MonsterDef(
        "melma", "Melma Verde", "2d6", ac=8, attack_bonus=2, damage="1d4",
        xp=25, initiative_mod=-2, traits=("veleno",), group=(1, 1), tiers=(1, 2), glyph="m",
    ),
    "lupo": MonsterDef(
        "lupo", "Lupo Feroce", "2d6+2", ac=13, attack_bonus=3, damage="1d6+1",
        xp=35, initiative_mod=3, group=(2, 3), tiers=(2,), glyph="l",
    ),
    "orco": MonsterDef(
        "orco", "Orco", "2d8+2", ac=14, attack_bonus=4, damage="1d8+2",
        xp=50, group=(1, 3), tiers=(2, 3), glyph="O",
    ),
    "zombi": MonsterDef(
        "zombi", "Zombi", "3d8", ac=10, attack_bonus=3, damage="1d8",
        xp=45, initiative_mod=-3, traits=("non-morto",), group=(2, 3), tiers=(2, 3), glyph="z",
    ),
    "ragno": MonsterDef(
        "ragno", "Ragno Gigante", "2d8+2", ac=14, attack_bonus=4, damage="1d6",
        xp=55, initiative_mod=2, traits=("veleno",), group=(1, 2), tiers=(2, 3), glyph="x",
    ),
    "cultista": MonsterDef(
        "cultista", "Cultista Incappucciato", "2d8", ac=12, attack_bonus=3, damage="1d6+1",
        xp=45, group=(2, 4), tiers=(3,), glyph="c",
    ),
    "ogre": MonsterDef(
        "ogre", "Ogre", "4d8+4", ac=14, attack_bonus=5, damage="2d6+2",
        xp=110, initiative_mod=-1, group=(1, 1), tiers=(3,), glyph="O",
    ),
    "spettro": MonsterDef(
        "spettro", "Spettro", "3d8+3", ac=15, attack_bonus=4, damage="1d8+1",
        xp=100, initiative_mod=2, traits=("non-morto",), group=(1, 2), tiers=(3,), glyph="S",
    ),
    # --- boss ---
    "grommok": MonsterDef(
        "grommok", "Grommok il Re Goblin", "4d8+8", ac=15, attack_bonus=5, damage="1d8+3",
        xp=200, initiative_mod=2, group=(1, 1), tiers=(), glyph="K", boss_of=1,
    ),
    "vhalsa": MonsterDef(
        "vhalsa", "Vhalsa, Regina dei Ragni", "6d8+12", ac=16, attack_bonus=6, damage="2d6+2",
        xp=400, initiative_mod=3, traits=("veleno",), group=(1, 1), tiers=(), glyph="W", boss_of=2,
    ),
    "orcus": MonsterDef(
        "orcus", "Orcus, Signore delle Ossa", "8d8+24", ac=17, attack_bonus=7, damage="2d8+3",
        xp=800, initiative_mod=1, traits=("non-morto", "boss"), group=(1, 1), tiers=(), glyph="&",
        boss_of=3,
    ),
}


def monsters_for_tier(tier: int) -> list[MonsterDef]:
    return [m for m in BESTIARY.values() if tier in m.tiers]


def boss_for_tier(tier: int) -> MonsterDef:
    for m in BESTIARY.values():
        if m.boss_of == tier:
            return m
    # oltre l'ultimo livello previsto: torna il boss piu' profondo
    return max(BESTIARY.values(), key=lambda m: m.boss_of)


# --------------------------------------------------------------------------
# Tesori e trappole
# --------------------------------------------------------------------------

#: Tabella del bottino per profondita': (chiave oggetto, peso).
LOOT_TABLES: dict[int, tuple[tuple[str, int], ...]] = {
    1: (("moneta_antica", 40), ("pozione_cura", 30), ("pugnale", 10),
        ("cuoio", 10), ("gemma", 10)),
    2: (("gemma", 30), ("pozione_cura", 25), ("spada_lunga", 15),
        ("cotta", 10), ("pozione_grande", 10), ("idolo", 10)),
    3: (("idolo", 25), ("pozione_grande", 25), ("lama_runica", 15),
        ("usbergo_nano", 10), ("corona", 15), ("gemma", 10)),
}


@dataclass(frozen=True, slots=True)
class TrapDef:
    key: str
    name: str
    save: Ability
    dc: int
    damage: str
    condition: str = ""
    rounds: int = 0
    description: str = ""


TRAPS: dict[str, TrapDef] = {
    "dardi": TrapDef("dardi", "Dardi Avvelenati", Ability.DES, 12, "1d6",
                     condition=str(Condition.VELENO), rounds=3,
                     description="Fori nel muro. Poi il sibilo."),
    "fossa": TrapDef("fossa", "Fossa Nascosta", Ability.DES, 11, "1d8",
                     description="Il pavimento non c'era mai stato."),
    "lama": TrapDef("lama", "Lama Pendolare", Ability.DES, 13, "2d6",
                    description="Un fischio, poi il sangue sul muro."),
    "runa": TrapDef("runa", "Runa Esplosiva", Ability.COS, 13, "2d6",
                    description="La runa sul pavimento si accende di rosso."),
    "gas": TrapDef("gas", "Gas Soporifero", Ability.COS, 12, "1d4",
                   condition=str(Condition.STORDITO), rounds=1,
                   description="Un odore dolciastro riempie la stanza."),
}


def traps_for_tier(tier: int) -> list[TrapDef]:
    if tier <= 1:
        return [TRAPS["dardi"], TRAPS["fossa"]]
    if tier == 2:
        return [TRAPS["dardi"], TRAPS["fossa"], TRAPS["gas"], TRAPS["lama"]]
    return list(TRAPS.values())


#: Benedizioni dei santuari: (chiave, etichetta).
SHRINE_BLESSINGS: tuple[tuple[str, str], ...] = (
    ("cura", "Le tue ferite si richiudono."),
    ("benedizione", "Ti senti guidato da una mano invisibile."),
    ("slot", "La mente si schiarisce: la magia torna a scorrere."),
)
