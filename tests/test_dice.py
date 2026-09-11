import pytest

from ddos.engine.dice import DiceSpec, Roller, parse_dice


def test_parse_forme_valide():
    assert parse_dice("2d6+3") == DiceSpec(2, 6, 3)
    assert parse_dice("d20") == DiceSpec(1, 20, 0)
    assert parse_dice("3d6-1") == DiceSpec(3, 6, -1)
    assert parse_dice(" 4 d 8 ") == DiceSpec(4, 8, 0)
    assert parse_dice(DiceSpec(1, 4)) == DiceSpec(1, 4)


@pytest.mark.parametrize("expr", ["", "d", "2x6", "0d6", "1d1", "2d6+", "abc"])
def test_parse_forme_invalide(expr):
    with pytest.raises(ValueError):
        parse_dice(expr)


def test_stesso_seme_stessa_sequenza():
    a = [Roller("SEME").d20() for _ in range(50)]
    b = [Roller("SEME").d20() for _ in range(50)]
    assert a == b


def test_semi_diversi_sequenze_diverse():
    a = [Roller("ALFA").d20() for _ in range(20)]
    b = [Roller("BETA").d20() for _ in range(20)]
    assert a != b


def test_ripresa_dal_contatore():
    """Ricostruire il roller dal contatore continua la stessa sequenza."""
    first = Roller("SEME")
    prefix = [first.d20() for _ in range(10)]
    resumed = Roller("SEME", first.counter)
    whole = Roller("SEME")
    assert [whole.d20() for _ in range(20)] == prefix + [resumed.d20() for _ in range(10)]


def test_contatore_avanza_di_uno_per_dado():
    r = Roller("SEME")
    r.roll("3d6+2")
    assert r.counter == 3


def test_intervalli():
    r = Roller("SEME")
    assert all(1 <= r.d(6) <= 6 for _ in range(500))
    assert all(-3 <= r.randint(-3, 3) <= 3 for _ in range(200))


def test_distribuzione_ragionevole():
    """Nessun bias grossolano: ogni faccia del d6 tra il 14% e il 19%."""
    r = Roller("DISTRIBUZIONE")
    counts = {i: 0 for i in range(1, 7)}
    for _ in range(6000):
        counts[r.d(6)] += 1
    assert all(840 <= c <= 1160 for c in counts.values()), counts


def test_roll_result_mostra_i_dadi():
    result = Roller("SEME").roll("2d6+3")
    assert len(result.dice) == 2
    assert result.total == sum(result.dice) + 3


def test_danno_non_scende_sotto_zero():
    assert Roller("SEME").roll("1d4-10").total == 0


def test_best_of_tiene_i_migliori():
    total, dice = Roller("SEME").best_of(4, 6, 3)
    assert len(dice) == 4
    assert total == sum(sorted(dice, reverse=True)[:3])


def test_shuffled_non_modifica_l_originale():
    original = list(range(10))
    shuffled = Roller("SEME").shuffled(original)
    assert original == list(range(10))
    assert sorted(shuffled) == original
