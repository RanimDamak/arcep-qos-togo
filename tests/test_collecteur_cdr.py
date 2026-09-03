"""Tests du parsing CollecteurCDR (sans base : on remplace _inserer).

Lancer :  python -m pytest tests/ -v      (depuis la racine du projet)
"""
import datetime as dt

from src.collecte.collecteur_cdr import CollecteurCDR, MAP_TYPE_CDR


def _collecteur_sans_db(mock=False):
    c = CollecteurCDR(mock_qos=mock)
    capture = {}
    c._inserer = lambda lignes: (capture.__setitem__("lignes", list(lignes)) or len(capture["lignes"]))
    return c, capture


def test_mapping_type_cdr():
    assert MAP_TYPE_CDR["gw"] == "PGW-CDR"
    assert MAP_TYPE_CDR["moc"] == "MSC-CDR"
    assert MAP_TYPE_CDR["smt"] == "MSC-CDR"


def test_parse_datetime_utc():
    c = CollecteurCDR()
    ts = c._parse_datetime("2026-06-24T10:32:27")
    assert ts == dt.datetime(2026, 6, 24, 10, 32, 27, tzinfo=dt.timezone.utc)
    assert c._parse_datetime(None) is None
    assert c._parse_datetime("pas une date") is None


def test_num():
    c = CollecteurCDR()
    assert c._num(2963.0) == 2963.0
    assert c._num("5347") == 5347.0
    assert c._num(None) is None
    assert c._num("") is None


def test_mapper_data_gw():
    c = CollecteurCDR()
    rec = {"cdrType": "gw", "cdrReference": "X1", "dateTime": "2026-06-24T10:00:00", "uplink": 100.0, "downlink": 200.0, "tarifPlan": None}
    ligne = c._mapper(rec)
    assert ligne.type_cdr == "PGW-CDR"
    assert ligne.uplink == 100.0 and ligne.downlink == 200.0
    # NB : LigneCDR n'a pas de forfait_id (enregistrement_cdr est 100% QoS, ne porte
    # jamais le forfait - voir schema.sql, table consommation_forfait pour ce lien).
    assert ligne.qos_negotiated_raw is None  # pas de qosNegotiated dans ce record -> QoS absente
    assert ligne.delay_class_score is None
    assert ligne.qos_spec_version == "absent"


def test_mapper_voix_moc():
    c = CollecteurCDR()
    rec = {"cdrType": "moc", "cdrReference": "Y2", "dateTime": "2025-08-01T17:59:02"}
    ligne = c._mapper(rec)
    assert ligne.type_cdr == "MSC-CDR"
    assert ligne.qos_spec_version == "voix"  # qualité voix via half_rate/hr_ratio, pas de décodage QoS
    assert ligne.qos_negotiated_raw is None


def test_mapper_voix_ignore_qos_negotiated_meme_si_present():
    # Un CDR voix ne doit JAMAIS être décodé QoS, même si le champ apparaît par erreur dans le JSON.
    c = CollecteurCDR()
    rec = {"cdrType": "moc", "dateTime": "2026-08-02T17:16:00", "qosNegotiated": "011B911F7396FEFE742B7878006400"}
    ligne = c._mapper(rec)
    assert ligne.qos_spec_version == "voix"
    assert ligne.qos_negotiated_raw is None
    assert ligne.delay_class_score is None


def test_mapper_type_inconnu_retourne_none():
    c = CollecteurCDR()
    assert c._mapper({"cdrType": "???", "dateTime": "2026-01-01T00:00:00"}) is None


def test_mapper_sans_date_retourne_none():
    c = CollecteurCDR()
    assert c._mapper({"cdrType": "gw"}) is None


def test_mapper_data_decode_qos_negotiated_reel():
    # Échantillon réel validé (voir Analyse_QoS_15octets.md) - Traffic Class=Interactive,
    # Delay class=3, Precedence=1, débit UL/DL=8640 kbps (plafond codé).
    c = CollecteurCDR()
    rec = {"cdrType": "gw", "dateTime": "2026-08-02T17:15:00", "qosNegotiated": "011B911F7396FEFE742B7878006400"}
    ligne = c._mapper(rec)
    assert ligne.qos_spec_version == "24.008"
    assert ligne.qos_negotiated_raw == bytes.fromhex("011B911F7396FEFE742B7878006400")
    assert ligne.delay_class_score == 3
    assert ligne.precedence_class_score == 1
    assert ligne.max_bitrate_dl_kbps == 8640


def test_mapper_data_qos_negotiated_malforme_ne_bloque_pas():
    c = CollecteurCDR()
    rec = {"cdrType": "gw", "dateTime": "2026-08-02T17:17:00", "qosNegotiated": "PAS_DU_HEX_VALIDE"}
    ligne = c._mapper(rec)
    assert ligne is not None            # le fichier ne doit pas crasher sur une ligne malformée
    assert ligne.qos_spec_version == "absent"
    assert ligne.qos_negotiated_raw is None


def test_mock_qos_marque_mock():
    c = CollecteurCDR(mock_qos=True)
    rec = {"cdrType": "gw", "dateTime": "2026-06-24T10:00:00"}
    ligne = c._mapper(rec)
    assert ligne.qos_spec_version == "MOCK"
    assert 1 <= ligne.delay_class_score <= 4
    assert 1 <= ligne.precedence_class_score <= 3


def test_mock_qos_jamais_applique_a_la_voix():
    c = CollecteurCDR(mock_qos=True)
    rec = {"cdrType": "moc", "dateTime": "2026-06-24T10:00:00"}
    ligne = c._mapper(rec)
    assert ligne.qos_spec_version == "voix"   # --mock ne doit jamais mocker la voix
    assert ligne.delay_class_score is None
