"""Client da terminale: chi agisce quando nessuno lo dice esplicitamente.

E' il punto che rendeva inutilizzabili Ladro e Chierico fuori dal
combattimento, perche' ogni comando finiva a chi guidava la compagnia.
"""

import pytest

from ddos.cli.play import (
    Console, attore_per, costruisci_azione, parse_party,
)
from ddos.engine import actions as A
from ddos.engine.actions import Action
from ddos.engine.entities import ClassId, Status
from ddos.engine.reduce import reduce
from ddos.engine.state import Phase, new_game
from ddos.render.ansi import PLAIN


class ConsoleMuta(Console):
    """Registra gli errori invece di stamparli."""

    def __init__(self):
        super().__init__(PLAIN)
        self.errori: list[str] = []

    def error(self, testo: str) -> None:
        self.errori.append(testo)

    def lines(self, righe, colore: str = "") -> None:
        pass


COMPAGNIA = [("Grommash", ClassId.GUERRIERO), ("Silfa", ClassId.LADRO),
             ("Zilla", ClassId.MAGO), ("Bard", ClassId.CHIERICO)]


def partita(seed="CLI", membri=COMPAGNIA):
    st = new_game("cli", seed)
    for indice, (nome, cls) in enumerate(membri, start=1):
        st, _ = reduce(st, Action(A.JOIN, f"p{indice}", nome, str(cls)))
    st, _ = reduce(st, Action(A.BEGIN))
    return st


def nome_di(st, azione):
    return st.char(azione.actor).name


# --- scelta automatica dell'attore -----------------------------------------


def test_avanscoperta_va_al_ladro_anche_se_guida_il_guerriero():
    st = partita()
    assert st.leader.name == "Grommash"
    attore, motivo = attore_per(st, "spia", ["n"])
    assert attore.name == "Silfa" and not motivo


def test_disinnescare_va_al_ladro():
    st = partita()
    attore, _ = attore_per(st, "disinnesca", [])
    assert attore.cls is ClassId.LADRO


def test_la_cura_va_al_chierico():
    st = partita()
    attore, motivo = attore_per(st, "lancia", ["cura"])
    assert attore.name == "Bard" and not motivo


def test_il_dardo_va_al_mago():
    st = partita()
    attore, _ = attore_per(st, "lancia", ["dardo"])
    assert attore.cls is ClassId.MAGO


def test_l_oggetto_lo_usa_chi_ce_l_ha():
    st = partita()
    for c in st.party:
        c.inventory.pop("pozione_cura", None)
    portatore = st.char("p3")          # il Mago, che non e' il capo
    portatore.add_item("pozione_cura")
    attore, _ = attore_per(st, "usa", ["pozione_cura"])
    assert attore is portatore


def test_muoversi_resta_al_capo():
    st = partita()
    for comando in ("n", "riposa", "scendi"):
        attore, _ = attore_per(st, comando, [])
        assert attore.id == st.leader_id, comando


def test_senza_candidati_il_motivo_e_esplicito():
    st = partita(membri=[("Grommash", ClassId.GUERRIERO)])
    attore, motivo = attore_per(st, "spia", ["n"])
    assert attore is None and "Ladro" in motivo

    attore, motivo = attore_per(st, "lancia", ["cura"])
    assert attore is None and "Chierico" in motivo

    attore, motivo = attore_per(st, "usa", ["lama_runica"])
    assert attore is None and "Lama Runica" in motivo


def test_senza_slot_nessun_lanciatore():
    st = partita()
    for c in st.party:
        c.spell_slots = 0
    attore, motivo = attore_per(st, "lancia", ["cura"])
    assert attore is None and "slot" in motivo


def test_chi_e_a_terra_non_viene_scelto():
    st = partita()
    ladro = next(c for c in st.party if c.cls is ClassId.LADRO)
    ladro.status = Status.MORENTE
    ladro.hp = 0
    attore, motivo = attore_per(st, "spia", ["n"])
    assert attore is None and "Ladro" in motivo


def test_in_combattimento_decide_l_iniziativa():
    st = partita("CLI-COMBAT")
    for _ in range(60):
        if st.phase is Phase.COMBATTIMENTO:
            break
        if st.over or not st.room.exits:
            pytest.skip("nessun combattimento con questo seme")
        st, _ = reduce(st, Action(A.MOVE, st.leader_id, value=sorted(st.room.exits)[0]))
    else:
        pytest.skip("nessun combattimento con questo seme")

    for comando in ("spia", "lancia", "a"):
        attore, _ = attore_per(st, comando, ["cura"])
        assert attore.id == st.combat.current_id, comando


# --- prefisso esplicito ----------------------------------------------------


def test_il_prefisso_forza_l_attore():
    st = partita()
    console = ConsoleMuta()
    azione = costruisci_azione(st, "zilla: cerca", console)
    assert azione is not None and nome_di(st, azione) == "Zilla"
    assert not console.errori


def test_il_prefisso_accetta_un_nome_parziale():
    st = partita()
    azione = costruisci_azione(st, "sil: cerca", ConsoleMuta())
    assert nome_di(st, azione) == "Silfa"


def test_prefisso_con_nome_sconosciuto():
    st = partita()
    console = ConsoleMuta()
    assert costruisci_azione(st, "gandalf: cerca", console) is None
    assert "gandalf" in console.errori[0]


def test_prefisso_senza_comando():
    st = partita()
    console = ConsoleMuta()
    assert costruisci_azione(st, "silfa:", console) is None
    assert "Silfa" in console.errori[0]


def test_il_prefisso_non_scavalca_l_iniziativa():
    st = partita("CLI-COMBAT")
    for _ in range(60):
        if st.phase is Phase.COMBATTIMENTO:
            break
        if st.over or not st.room.exits:
            pytest.skip("nessun combattimento con questo seme")
        st, _ = reduce(st, Action(A.MOVE, st.leader_id, value=sorted(st.room.exits)[0]))
    else:
        pytest.skip("nessun combattimento con questo seme")

    fuori_turno = next(c for c in st.standing_party if c.id != st.combat.current_id)
    console = ConsoleMuta()
    assert costruisci_azione(st, f"{fuori_turno.name}: difendi", console) is None
    assert "iniziativa" in console.errori[0]


# --- comandi che funzionano comunque ---------------------------------------


def test_i_comandi_di_sola_lettura_reggono_una_compagnia_a_terra():
    """Con tutti a terra non c'e' un attore, ma 'aiuto' e 'scheda' devono
    comunque rispondere invece di lamentarsi."""
    st = partita()
    for c in st.party:
        c.status = Status.MORENTE
        c.hp = 0
    console = ConsoleMuta()
    for comando in ("aiuto", "mappa", "scheda"):
        assert costruisci_azione(st, comando, console) is None
    assert not console.errori


def test_comando_sconosciuto():
    st = partita()
    console = ConsoleMuta()
    assert costruisci_azione(st, "danza", console) is None
    assert "danza" in console.errori[0]


def test_esci_solleva_uscita():
    st = partita()
    with pytest.raises(SystemExit):
        costruisci_azione(st, "esci", ConsoleMuta())


# --- composizione della compagnia ------------------------------------------


def test_parse_party():
    membri = parse_party("Grommash:g, Silfa:ladro,Zilla:m")
    assert [n for n, _ in membri] == ["Grommash", "Silfa", "Zilla"]
    assert [c for _, c in membri] == [ClassId.GUERRIERO, ClassId.LADRO, ClassId.MAGO]


def test_parse_party_classe_sconosciuta():
    with pytest.raises(SystemExit):
        parse_party("Tizio:paladino")
