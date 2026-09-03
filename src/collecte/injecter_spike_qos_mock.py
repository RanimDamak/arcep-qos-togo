"""Injecte une observation_horaire avec des valeurs degradees sur 2025-08-02 17h, pour tester AlerteQoS avec une vraie deviation positive sur les 3 indicateurs (C, QoS, HR).
"""
from __future__ import annotations

import datetime as dt

from src.donnees.db import connexion

BUCKET_SPIKE = dt.datetime(2025, 8, 2, 17, 0, tzinfo=dt.timezone.utc)

# Profil 17h : c_moy=0.029, c_sigma=0.0027 -> seuil 3s = ~0.037
#              qos_moy=30.17, qos_sigma=2.36 -> seuil 3s = ~37.2
#              hr_moy=0.045, hr_sigma=0.0071 -> seuil 3s = ~0.066
CONGESTION_SPIKE = 0.06   # bien au-dessus du seuil ~0.037
QOS_SPIKE = 55.0          # bien au-dessus du seuil ~37.2
HR_SPIKE = 0.09           # bien au-dessus du seuil ~0.066


def inserer():
    with connexion() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO observation_horaire (bucket, congestion, qos, half_rate)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (bucket) DO UPDATE SET
                    congestion = EXCLUDED.congestion,
                    qos = EXCLUDED.qos,
                    half_rate = EXCLUDED.half_rate
                """,
                (BUCKET_SPIKE, CONGESTION_SPIKE, QOS_SPIKE, HR_SPIKE),
            )
    print(f"Observation degradee inseree pour {BUCKET_SPIKE.isoformat()}")


if __name__ == "__main__":
    inserer()
    