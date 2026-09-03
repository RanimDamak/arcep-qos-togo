"""Genere des lignes de consommation_forfait factices (est_mock=TRUE) pour tester AgregateurTrafic de bout en bout, en attendant la confirmation des
codes forfait reels (847/892 absents du catalogue YAS - voir README SS7). Reutilise la fenetre deja validee cote QoS : 2025-08-01, heures 17h/18h.

Usage :
    python -m src.collecte.generer_mock_consommation
"""
from __future__ import annotations

import datetime as dt
import random

from src.donnees.db import connexion

CODES_MOCK = ["109", "111", "114"]

HEURES = [
    dt.datetime(2025, 7, 31, 17, 0, tzinfo=dt.timezone.utc),
    dt.datetime(2025, 7, 31, 18, 0, tzinfo=dt.timezone.utc),
    dt.datetime(2025, 8, 1, 17, 0, tzinfo=dt.timezone.utc),
    dt.datetime(2025, 8, 1, 18, 0, tzinfo=dt.timezone.utc),
]

LIGNES_PAR_HEURE_PAR_CODE = 4


def generer_lignes():
    lignes = []
    for heure in HEURES:
        for code in CODES_MOCK:
            for _ in range(LIGNES_PAR_HEURE_PAR_CODE):
                offset_min = random.randint(0, 59)
                offset_sec = random.randint(0, 59)
                ts = heure + dt.timedelta(minutes=offset_min, seconds=offset_sec)
                servi = f"9000{random.randint(1000, 9999)}"

                if random.random() < 0.5:
                    lignes.append((ts, servi, code, "voiceCall", random.randint(10, 300), None, True))
                else:
                    lignes.append((ts, servi, code, "dataSession", None, float(random.randint(1_000, 500_000)), True))
    return lignes


def inserer(lignes):
    cols = ("bucket", "served_isdn", "forfait_id", "cdr_type", "duration", "data_volume", "est_mock")
    with connexion() as conn:
        with conn.cursor() as cur:
            with cur.copy(f"COPY consommation_forfait ({', '.join(cols)}) FROM STDIN") as copy:
                for ligne in lignes:
                    copy.write_row(ligne)
    return len(lignes)


if __name__ == "__main__":
    lignes = generer_lignes()
    n = inserer(lignes)
    print(f"{n} lignes mock inserees dans consommation_forfait (est_mock=TRUE)")
    print(f"Codes utilises : {CODES_MOCK}")
    print(f"Heures : {[h.isoformat() for h in HEURES]}")