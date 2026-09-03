"""Injecte des lignes observation_horaire mock pour 2025-07-31, 17h/18h, afin de completer un profil QoS sur 2 jours (2025-07-31 + 2025-08-01
deja valide) et tester AlerteQoS avec un vrai sigma non-NULL. Contourne CollecteurQualcop/CollecteurCDR : ceux-ci sont lies aux dates reelles
des fichiers sources et ne permettent pas de cibler une date synthetique arbitraire.

Usage :
    python -m src.collecte.generer_mock_observation
"""
from __future__ import annotations

import datetime as dt

from src.donnees.db import connexion

LIGNES = [
    # (bucket, congestion, qos, half_rate)
    (dt.datetime(2025, 7, 31, 17, 0, tzinfo=dt.timezone.utc), 0.031, 28.50, 0.05),
    (dt.datetime(2025, 7, 31, 18, 0, tzinfo=dt.timezone.utc), 0.024, 95.20, 0.06),
]


def inserer():
    with connexion() as conn:
        with conn.cursor() as cur:
            for bucket, congestion, qos, half_rate in LIGNES:
                cur.execute(
                    """
                    INSERT INTO observation_horaire (bucket, congestion, qos, half_rate)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (bucket) DO UPDATE SET
                        congestion = EXCLUDED.congestion,
                        qos = EXCLUDED.qos,
                        half_rate = EXCLUDED.half_rate
                    """,
                    (bucket, congestion, qos, half_rate),
                )
    print(f"{len(LIGNES)} lignes observation_horaire inserees/mises a jour pour 2025-07-31")


if __name__ == "__main__":
    inserer()