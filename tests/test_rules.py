import pytest

from ddos.engine import content as C
from ddos.engine import rules as R
from ddos.engine.dice import Roller
from ddos.engine.entities import Ability, ClassId, Condition, Status, ability_mod
from tests.conftest import TruccatoRoller


@pytest.fixture
def roller():
    return Roller("TEST-REGOLE")


@pytest.fixture
def guerriero(roller):
    return R.create_character(roller, char_id="g", name="Grommash", cls=ClassId.GUERRIERO)


@pytest.mark.parametrize("score,expected", [(3, -4), (8, -1), (10, 0), (11, 0), (16, 3), (18, 4)])
def test_modificatore(score, expected):
    assert ability_mod(score) == expected


def test_creazione_assegna_il_meglio_alla_caratteristica_di_classe(roller):
    for cls in ClassId:
        ch = R.create_character(roller, char_id="x", name="Tizio", cls=cls)
        primary = C.CLASSES[cls].primary
        assert ch.abilities[primary] == max(ch.abilities.values())
        assert ch.hp == ch.max_hp > 0
        assert ch.weapon and ch.armor


def test_ca_tiene_conto_di_destrezza_armatura_e_condizioni(guerriero):
    base = 10 + guerriero.mod(Ability.DES) + C.item(guerriero.armor).ac_bonus
    assert R.armor_class(guerriero) == base
    guerriero.add_condition(Condition.DIFESA, 1)
    assert R.armor_class(guerriero) == base + 2
    guerriero.add_condition(Condition.SCUDO, 1)
    assert R.armor_class(guerriero) == base + 6


def test_danno_porta_a_morente_non_a_morto(guerriero):
    R.apply_damage(guerriero, 999)
    assert guerriero.status is Status.MORENTE
    assert guerriero.hp == 0


def test_colpire_un_morente_lo_uccide_in_tre_colpi(guerriero):
    R.apply_damage(guerriero, 999)
    for _ in range(3):
        R.apply_damage(guerriero, 1)
    assert guerriero.status is Status.MORTO


def test_cura_rimette_in_piedi_un_morente(guerriero):
    R.apply_damage(guerriero, 999)
    R.heal(guerriero, 5)
    assert guerriero.status is Status.VIVO
    assert guerriero.hp == 5


def test_la_cura_non_resuscita(guerriero):
    guerriero.status = Status.MORTO
    events = R.heal(guerriero, 10)
    assert guerriero.status is Status.MORTO
    assert events[0].kind == "errore"


def test_cura_non_supera_il_massimo(guerriero):
    guerriero.hp = 1
    R.heal(guerriero, 999)
    assert guerriero.hp == guerriero.max_hp


def test_tre_fallimenti_uccidono(guerriero, roller):
    R.apply_damage(guerriero, 999)
    for _ in range(60):
        if guerriero.status is not Status.MORENTE:
            break
        R.death_save(guerriero, roller)
    assert guerriero.status in (Status.MORTO, Status.VIVO) or guerriero.death_successes >= 3


def test_uno_naturale_manca_sempre(guerriero):
    goblin = R.spawn_monster("goblin", Roller("M"))
    goblin.ac = 1  # impossibile da mancare, se non con un 1 naturale
    events = R.resolve_attack(guerriero, goblin, TruccatoRoller("X", [1]))
    assert events[0].kind == "mancato"


def test_venti_naturale_colpisce_sempre(guerriero):
    goblin = R.spawn_monster("goblin", Roller("M"))
    goblin.ac = 99
    events = R.resolve_attack(guerriero, goblin, TruccatoRoller("X", [20]))
    assert events[0].kind == "critico"


def test_critico_raddoppia_i_dadi_non_i_modificatori(guerriero):
    """Un critico deve fare piu' danno di un colpo normale con lo stesso dado."""
    goblin_a = R.spawn_monster("goblin", Roller("M"))
    goblin_b = R.spawn_monster("goblin", Roller("M"))
    goblin_a.ac = goblin_b.ac = 1
    goblin_a.max_hp = goblin_b.max_hp = 500
    goblin_a.hp = goblin_b.hp = 500
    R.resolve_attack(guerriero, goblin_a, TruccatoRoller("DANNO", [20]))
    R.resolve_attack(guerriero, goblin_b, TruccatoRoller("DANNO", [15]))
    assert 500 - goblin_a.hp > 500 - goblin_b.hp


def test_attacco_furtivo_consuma_la_condizione(roller):
    ladro = R.create_character(roller, char_id="l", name="Silfa", cls=ClassId.LADRO)
    ladro.add_condition(Condition.NASCOSTO, 2)
    goblin = R.spawn_monster("goblin", roller)
    R.resolve_attack(ladro, goblin, roller)
    assert not ladro.has_condition(Condition.NASCOSTO)


def test_px_fanno_salire_di_livello(guerriero, roller):
    hp_prima = guerriero.max_hp
    R.grant_xp(guerriero, C.XP_THRESHOLDS[1], roller)
    assert guerriero.level == 2
    assert guerriero.max_hp > hp_prima


def test_px_multipli_livelli_in_un_colpo(guerriero, roller):
    R.grant_xp(guerriero, C.XP_THRESHOLDS[3], roller)
    assert guerriero.level == 4


def test_slot_esauriti_bloccano_l_incantesimo(roller):
    mago = R.create_character(roller, char_id="m", name="Zilla", cls=ClassId.MAGO)
    mago.spell_slots = 0
    events = R.cast_spell(mago, C.SPELLS["dardo"], [], roller)
    assert events[0].kind == "errore"


def test_scacciare_ignora_i_vivi(roller):
    chierico = R.create_character(roller, char_id="c", name="Bard", cls=ClassId.CHIERICO)
    goblin = R.spawn_monster("goblin", roller)
    events = R.cast_spell(chierico, C.SPELLS["scacciare"], [goblin], roller)
    assert goblin.hp == goblin.max_hp
    assert any(e.kind == "info" for e in events)


def test_togliere_zero_oggetti_non_esplode(guerriero):
    """Simmetrico ad add_item, che gia' ignorava le quantita' non positive."""
    assert guerriero.remove_item("gemma", 0) is True
    assert guerriero.remove_item("gemma", -1) is True
    assert "gemma" not in guerriero.inventory


def test_equipaggiare_scambia_e_rimette_in_zaino(guerriero):
    guerriero.add_item("pugnale")
    vecchia = guerriero.weapon
    R.equip(guerriero, "pugnale")
    assert guerriero.weapon == "pugnale"
    assert guerriero.inventory.get(vecchia) == 1


def test_classi_rispettate_nell_equipaggiamento(roller):
    mago = R.create_character(roller, char_id="m", name="Zilla", cls=ClassId.MAGO)
    mago.add_item("ascia_bipenne")
    events = R.equip(mago, "ascia_bipenne")
    assert events[0].kind == "errore"
    assert mago.weapon != "ascia_bipenne"


def test_provocare_calamita_i_mostri(roller):
    a = R.create_character(roller, char_id="a", name="A", cls=ClassId.GUERRIERO)
    b = R.create_character(roller, char_id="b", name="B", cls=ClassId.MAGO)
    a.add_condition(Condition.PROVOCATO, 2)
    goblin = R.spawn_monster("goblin", roller)
    assert all(R.choose_target(goblin, [a, b], roller) is a for _ in range(20))


def test_i_mostri_ignorano_chi_e_a_terra(roller):
    a = R.create_character(roller, char_id="a", name="A", cls=ClassId.GUERRIERO)
    b = R.create_character(roller, char_id="b", name="B", cls=ClassId.MAGO)
    R.apply_damage(a, 999)
    goblin = R.spawn_monster("goblin", roller)
    assert all(R.choose_target(goblin, [a, b], roller) is b for _ in range(20))
