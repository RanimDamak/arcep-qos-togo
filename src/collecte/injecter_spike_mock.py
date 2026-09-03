"""Injecte un pic artificiel de trafic sur un jour/heure donne, pour un forfait donne, afin de tester DetecteurAnomalie (regle 3-sigma) avec une vraie deviation positive.
A executer APRES avoir construit le profil de reference sur une fenetre qui n'inclut PAS ce jour (sinon le pic contamine son propre profil et s'auto-normalise).

Usage :
    python -m src.collecte.injecter_spike_mock
"""
from __future__ import annotations

import datetime as dt

from src.donnees.db import connexion

FORFAIT_SPIKE = "109"
HEURE_SPIKE = dt.datetime(2025, 8, 2, 17, 0, tzinfo=dt.timezone.utc)
AUTRES_CODES = ["111", "114"]


def inserer_lignes():
    lignes = []
    # Pic massif sur 109 : volume largement au-dessus de sa moyenne historique (~1.5M)
    lignes.append((HEURE_SPIKE, "90009999", FORFAIT_SPIKE, "voiceCall", 5000, None, True))
    lignes.append((HEURE_SPIKE, "90009998", FORFAIT_SPIKE, "dataSession", None, 8_000_000.0, True))
    # Volumes normaux sur les 2 autres, pour que le total/alpha reste comparable
    for code in AUTRES_CODES:
        lignes.append((HEURE_SPIKE, "90008888", code, "voiceCall", 100, None, True))
        lignes.append((HEURE_SPIKE, "90008887", code, "dataSession", None, 500_000.0, True))

    cols = ("bucket", "served_isdn", "forfait_id", "cdr_type", "duration", "data_volume", "est_mock")
    with connexion() as conn:
        with conn.cursor() as cur:
            with cur.copy(f"COPY consommation_forfait ({', '.join(cols)}) FROM STDIN") as copy:
                for l in lignes:
                    copy.write_row(l)
    print(f"{len(lignes)} lignes injectees pour {HEURE_SPIKE.isoformat()} (pic sur {FORFAIT_SPIKE})")


if __name__ == "__main__":
    inserer_lignes()