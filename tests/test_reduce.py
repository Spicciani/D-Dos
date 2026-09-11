"""Test del riduttore: il contratto pubblico del motore."""

import json

import pytest

from ddos.engine import actions as A
from ddos.engine.actions import Action
from ddos.engine.dungeon import RoomKind
from ddos.engine.entities import ClassId, Condition, Status
from ddos.engine.reduce import reduce
from ddos.engine.state import GameState, Phase, new_game

PARTY = [
    ("pg1", "Grommash", ClassId.GUERRIERO),
    ("pg2", "Silfa", ClassId.LADRO),
    ("pg3", "Zilla", ClassId.MAGO),
    ("pg4", "Bard", ClassId.CHIERICO),
]


def partita(seed="TEST", membri=PARTY, avvia=True):
    st = new_game("g1", seed)
    for cid, nome, cls in membri:
        st, _ = reduce(st, Action(A.JOIN, cid, nome, str(cls)))
    if avvia:
        st, _ = reduce(st, Action(A.BEGIN))
    return st


# --- lobby -----------------------------------------------------------------


def test_join_crea_il_personaggio():
    st = partita(avvia=False)
    assert [c.name for c in st.party] == ["Grommash", "Silfa", "Zilla", "Bard"]
    assert st.leader_id == "pg1"


def test_non_si_entra_due_volte():
    st = partita(avvia=False)
    st2, evs = reduce(st, Action(A.JOIN, "pg1", "Altro", str(ClassId.MAGO)))
    assert evs[0].kind == "errore"
    assert st2 is st


def test_nomi_duplicati_rifiutati():
    st = partita(avvia=False)
    _, evs = reduce(st, Action(A.JOIN, "pg9", "grommash", str(ClassId.MAGO)))
    assert evs[0].kind == "errore"


def test_classe_inesistente_rifiutata():
    st = new_game("g", "S")
    _, evs = reduce(st, Action(A.JOIN, "pg1", "Tizio", "paladino"))
    assert evs[0].kind == "errore"


def test_party_pieno():
    membri = [(f"pg{i}", f"Eroe{i}", ClassId.GUERRIERO) for i in range(1, 8)]
    st = new_game("g", "S")
    for cid, nome, cls in membri:
        st, evs = reduce(st, Action(A.JOIN, cid, nome, str(cls)))
    assert len(st.party) == 6
    assert evs[0].kind == "errore"


def test_begin_senza_party_rifiutato():
    _, evs = reduce(new_game("g", "S"), Action(A.BEGIN))
    assert evs[0].kind == "errore"


def test_begin_genera_il_livello_e_apre_l_esplorazione():
    st = partita()
    assert st.phase in (Phase.ESPLORAZIONE, Phase.COMBATTIMENTO)
    assert st.level is not None
    assert st.room_id == st.level.entry_id


# --- invarianti ------------------------------------------------------------


def test_azione_invalida_non_cambia_nulla():
    st = partita()
    prima = json.dumps(st.to_dict(), sort_keys=True)
    st2, evs = reduce(st, Action(A.MOVE, "pg1", value="direzione-inesistente"))
    assert evs[0].kind == "errore"
    assert json.dumps(st2.to_dict(), sort_keys=True) == prima


def test_azione_invalida_non_consuma_dadi():
    st = partita()
    st2, _ = reduce(st, Action(A.MOVE, "pg1", value="xyz"))
    assert st2.roll_count == st.roll_count
    assert st2.turn_seq == st.turn_seq


def test_lo_stato_in_ingresso_non_viene_modificato():
    st = partita()
    prima = json.dumps(st.to_dict(), sort_keys=True)
    reduce(st, Action(A.SEARCH, "pg1"))
    assert json.dumps(st.to_dict(), sort_keys=True) == prima


def test_turn_seq_avanza_solo_sulle_azioni_valide():
    st = partita()
    st2, _ = reduce(st, Action(A.SEARCH, "pg1"))
    assert st2.turn_seq == st.turn_seq + 1


def test_personaggio_inesistente():
    st = partita()
    _, evs = reduce(st, Action(A.SEARCH, "nessuno"))
    assert evs[0].kind == "errore"


# --- esplorazione ----------------------------------------------------------


def test_solo_il_capo_decide_dove_andare():
    st = partita()
    if st.phase is not Phase.ESPLORAZIONE:
        pytest.skip("il seme inizia in combattimento")
    direzione = sorted(st.room.exits)[0]
    _, evs = reduce(st, Action(A.MOVE, "pg2", value=direzione))
    assert evs[0].kind == "errore"
    st2, evs = reduce(st, Action(A.MOVE, "pg1", value=direzione))
    assert st2.room_id != st.room_id


def test_il_capo_si_puo_cambiare():
    st = partita(avvia=False)
    st, _ = reduce(st, Action(A.SET_LEADER, "pg1", "pg3"))
    assert st.leader_id == "pg3"


def test_da_soli_si_decide_da_soli():
    st = partita(membri=[("pg1", "Solo", ClassId.LADRO)])
    if st.phase is not Phase.ESPLORAZIONE:
        pytest.skip("il seme inizia in combattimento")
    direzione = sorted(st.room.exits)[0]
    st2, evs = reduce(st, Action(A.MOVE, "pg1", value=direzione))
    assert st2.room_id != st.room_id


def test_scendere_richiede_la_scala():
    st = partita()
    _, evs = reduce(st, Action(A.DESCEND, "pg1"))
    assert evs[0].kind == "errore"


def test_solo_il_ladro_disinnesca():
    st = partita()
    _, evs = reduce(st, Action(A.DISARM, "pg1"))
    assert evs[0].kind == "errore"
    assert "Ladro" in evs[0].text


def test_solo_il_ladro_va_in_avanscoperta():
    st = partita()
    _, evs = reduce(st, Action(A.SCOUT, "pg3", value=sorted(st.room.exits)[0]))
    assert evs[0].kind == "errore"


def test_avanscoperta_produce_un_messaggio_privato():
    st = partita("SCOUT")
    direzione = sorted(st.room.exits)[0]
    for _ in range(40):  # la prova di DES puo' fallire: si riprova
        st2, evs = reduce(st, Action(A.SCOUT, "pg2", value=direzione))
        sussurri = [e for e in evs if e.kind == "sussurro"]
        if sussurri:
            assert sussurri[0].data["to"] == "pg2"
            return
        st = st2
    pytest.fail("l'avanscoperta non e' mai riuscita")


def test_il_chierico_cura_fuori_dal_combattimento():
    st = partita()
    st.party[0].hp = 1
    st2, evs = reduce(st, Action(A.CAST, "pg4", "pg1", "cura"))
    assert st2.char("pg1").hp > 1


def test_incantesimi_offensivi_vietati_fuori_dal_combattimento():
    st = partita()
    if st.phase is Phase.COMBATTIMENTO:
        pytest.skip("il seme inizia in combattimento")
    _, evs = reduce(st, Action(A.CAST, "pg3", "x", "dardo"))
    assert evs[0].kind == "errore"


# --- combattimento ---------------------------------------------------------


def trova_combattimento(seed_base="COMBAT", tentativi=60):
    """Porta una partita fino al primo combattimento, qualunque seme serva."""
    for n in range(tentativi):
        st = partita(f"{seed_base}-{n}")
        for _ in range(40):
            if st.phase is Phase.COMBATTIMENTO:
                return st
            if st.over or st.phase is not Phase.ESPLORAZIONE:
                break
            uscite = sorted(st.room.exits)
            if not uscite:
                break
            st, _ = reduce(st, Action(A.MOVE, st.leader_id, value=uscite[0]))
    pytest.fail("nessun combattimento trovato")


def test_il_combattimento_si_ferma_sul_turno_di_un_giocatore():
    st = trova_combattimento()
    assert st.combat is not None
    assert st.char(st.combat.current_id) is not None


def test_non_si_agisce_fuori_dal_proprio_turno():
    st = trova_combattimento()
    altro = next(c for c in st.standing_party if c.id != st.combat.current_id)
    bersaglio = st.live_monsters[0]
    _, evs = reduce(st, Action(A.ATTACK, altro.id, bersaglio.id))
    assert evs[0].kind == "errore"
    assert "turno" in evs[0].text


def test_bersaglio_inesistente_rifiutato():
    st = trova_combattimento()
    _, evs = reduce(st, Action(A.ATTACK, st.combat.current_id, "mostro-fantasma"))
    assert evs[0].kind == "errore"


def test_l_ordine_di_iniziativa_include_tutti():
    st = trova_combattimento()
    attesi = {c.id for c in st.standing_party} | {m.id for m in st.combat.monsters}
    assert set(st.combat.order) == attesi


def test_difendersi_alza_la_ca_e_passa_il_turno():
    st = trova_combattimento()
    attore = st.combat.current_id
    st2, _ = reduce(st, Action(A.DEFEND, attore))
    if st2.phase is Phase.COMBATTIMENTO:
        assert st2.combat.current_id != attore or st2.combat.round > st.combat.round


def test_il_tick_mette_in_guardia_chi_non_risponde():
    st = trova_combattimento()
    attore = st.combat.current_id
    st2, evs = reduce(st, Action(A.TICK))
    assert any(e.kind == "turno" for e in evs)
    assert st2.char(attore).has_condition(Condition.DIFESA) or st2.phase is not Phase.COMBATTIMENTO


def test_solo_il_guerriero_fa_il_colpo_poderoso():
    st = trova_combattimento()
    for _ in range(30):
        attore = st.char(st.combat.current_id) if st.combat else None
        if attore is None:
            break
        if attore.cls is not ClassId.GUERRIERO:
            _, evs = reduce(st, Action(A.POWER, attore.id, st.live_monsters[0].id))
            assert evs[0].kind == "errore"
            return
        st, _ = reduce(st, Action(A.DEFEND, attore.id))
        if st.phase is not Phase.COMBATTIMENTO:
            break
    pytest.skip("nessun non-guerriero ha avuto il turno")


def test_uccidere_tutti_i_mostri_chiude_il_combattimento():
    st = trova_combattimento()
    for m in st.combat.monsters:
        m.hp = 1
    for _ in range(60):
        if st.phase is not Phase.COMBATTIMENTO:
            break
        attore = st.char(st.combat.current_id)
        vivi = st.live_monsters
        if not vivi:
            break
        st, _ = reduce(st, Action(A.ATTACK, attore.id, vivi[0].id))
        for m in st.combat.monsters if st.combat else []:
            m.hp = min(m.hp, 1)
    assert st.phase is not Phase.COMBATTIMENTO
    assert st.combat is None


def test_la_vittoria_assegna_px():
    st = trova_combattimento()
    px_prima = {c.id: c.xp for c in st.party}
    for m in st.combat.monsters:
        m.hp = 1
    for _ in range(60):
        if st.phase is not Phase.COMBATTIMENTO:
            break
        attore = st.char(st.combat.current_id)
        vivi = st.live_monsters
        if not vivi:
            break
        st, _ = reduce(st, Action(A.ATTACK, attore.id, vivi[0].id))
        for m in st.combat.monsters if st.combat else []:
            m.hp = min(m.hp, 1)
    assert any(c.xp > px_prima[c.id] for c in st.party if not c.dead)


def test_party_annientato_e_sconfitta():
    st = trova_combattimento()
    for c in st.party:
        c.hp = 0
        c.status = Status.MORENTE
    st, evs = reduce(st, Action(A.TICK))
    assert st.phase is Phase.SCONFITTA


def test_non_si_gioca_a_partita_finita():
    st = trova_combattimento()
    st.phase = Phase.VITTORIA
    _, evs = reduce(st, Action(A.SEARCH, "pg1"))
    assert evs[0].kind == "errore"


def test_chi_e_a_terra_non_agisce():
    st = trova_combattimento()
    vittima = st.party[0]
    vittima.status = Status.MORENTE
    vittima.hp = 0
    _, evs = reduce(st, Action(A.ATTACK, vittima.id, st.live_monsters[0].id))
    assert evs[0].kind == "errore"


# --- determinismo ----------------------------------------------------------


def test_stesso_seme_stesse_azioni_stessi_eventi():
    azioni = [Action(A.SEARCH, "pg1"), Action(A.SEARCH, "pg2"), Action(A.REST, "pg1")]

    def esegui():
        st = partita("GOLDEN")
        testi = []
        for azione in azioni:
            st, evs = reduce(st, azione)
            testi.extend(e.text for e in evs)
        return st.to_dict(), testi

    primo, secondo = esegui(), esegui()
    assert primo == secondo


def test_ricaricare_lo_stato_prosegue_identico():
    """Salvare e ricaricare a meta' partita non cambia un solo dado."""
    st = partita("RIPRESA")
    st, _ = reduce(st, Action(A.SEARCH, "pg1"))

    diretto, eventi_diretti = reduce(st, Action(A.REST, "pg1"))
    ricaricato = GameState.from_dict(json.loads(json.dumps(st.to_dict())))
    ripreso, eventi_ripresi = reduce(ricaricato, Action(A.REST, "pg1"))

    assert diretto.to_dict() == ripreso.to_dict()
    assert [e.text for e in eventi_diretti] == [e.text for e in eventi_ripresi]
