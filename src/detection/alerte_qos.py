"""AlerteQoS - Etape 6 du document technique (SS4.5). Compare les 3 indicateurs observes (congestion, QoS negociee, taux half-rate)
a leur profil historique (profil_qos_snapshot), regle a 3-sigma, UN SEUL SENS (degradation uniquement - Ck>=..., QoSk>=..., HRk>=...).

Note : le document technique decrit la regle QoS comme un intervalle bilateral (∉ I3), mais c'est traite ici comme une incoherence de redaction -
les 2 autres indicateurs (C, HR) sont clairement a sens unique (degradation), et on ne cherche jamais une "QoS anormalement bonne".
Decision alignee sur les 2 autres indicateurs, a sens unique partout.

Chaque indicateur est evalue INDEPENDAMMENT : si son profil (moyenne/sigma) est NULL (historique insuffisant), cet indicateur est simplement
ignore pour ce couple (bucket_ref, heure) - jamais traite comme "normal" par defaut. Un seul indicateur en alerte suffit a declencher.

Ecrit dans evenement_detecte (type_evenement='ALERTE_QOS', indicateur='C'/'QoS'/'HR',
score=valeur observee, seuil=Vk+3*sigma^k, niveau NULL - non applicable a ce type).
"""
from __future__ import annotations

import argparse
import datetime as dt
from typing import Optional


class AlerteQoS:
    """Detecte les depassements 3-sigma sur congestion/QoS/half-rate, heure par heure."""

    _INDICATEURS = [
        # (nom_indicateur, colonne_observee, colonne_moyenne_profil, colonne_sigma_profil)
        ("C",   "congestion", "c_moy",   "c_sigma"),
        ("QoS", "qos",        "qos_moy", "qos_sigma"),
        ("HR",  "half_rate",  "hr_moy",  "hr_sigma"),
    ]

    def detecter_heure(self, bucket: dt.datetime, bucket_ref: dt.datetime) -> dict:
        from src.donnees.db import connexion

        bucket = bucket.replace(minute=0, second=0, microsecond=0)
        bucket_ref = bucket_ref.replace(hour=0, minute=0, second=0, microsecond=0)
        heure = bucket.hour

        n_total = 0
        with connexion() as conn:
            with conn.cursor() as cur:
                for indicateur, col_obs, col_moy, col_sigma in self._INDICATEURS:
                    cur.execute(
                        f"""
                        WITH observe AS (
                            SELECT {col_obs} AS valeur FROM observation_horaire WHERE bucket = %s
                        ),
                        profil AS (
                            SELECT {col_moy} AS moy, {col_sigma} AS sigma
                            FROM profil_qos_snapshot WHERE bucket_ref = %s AND heure = %s
                        )
                        INSERT INTO evenement_detecte (bucket, type_evenement, indicateur, score, seuil)
                        SELECT %s, 'ALERTE_QOS', %s, o.valeur, (p.moy + 3 * p.sigma)
                        FROM observe o, profil p
                        WHERE o.valeur IS NOT NULL
                            AND p.sigma IS NOT NULL       -- historique insuffisant -> indicateur ignore
                            AND o.valeur >= (p.moy + 3 * p.sigma)
                        ON CONFLICT (bucket, type_evenement, COALESCE(forfait_id, ''), COALESCE(indicateur, ''))
                        DO UPDATE SET score = EXCLUDED.score, seuil = EXCLUDED.seuil
                        """,
                        (bucket, bucket_ref, heure, bucket, indicateur),
                    )
                    n_total += cur.rowcount
        return {"bucket": bucket.isoformat(), "alertes_detectees": n_total}

    def detecter_periode(self, debut: dt.datetime, fin: dt.datetime, bucket_ref: dt.datetime) -> list[dict]:
        resultats = []
        bucket = debut.replace(minute=0, second=0, microsecond=0)
        while bucket < fin:
            resultats.append(self.detecter_heure(bucket, bucket_ref))
            bucket += dt.timedelta(hours=1)
        return resultats


def _parse_date(valeur: str) -> dt.datetime:
    naive_ou_aware = dt.datetime.fromisoformat(valeur)
    if naive_ou_aware.tzinfo is None:
        return naive_ou_aware.replace(tzinfo=dt.timezone.utc)
    return naive_ou_aware


def main(argv: Optional[list[str]] = None) -> None:
    p = argparse.ArgumentParser(description="Detecte les alertes QoS (C/QoS/HR, regle 3-sigma, Etape 6).")
    p.add_argument("--debut", required=True)
    p.add_argument("--fin", required=True)
    p.add_argument("--bucket-ref", required=True)
    args = p.parse_args(argv)

    detecteur = AlerteQoS()
    resultats = detecteur.detecter_periode(_parse_date(args.debut), _parse_date(args.fin), _parse_date(args.bucket_ref))
    total = sum(r["alertes_detectees"] for r in resultats)
    print(f"=== {len(resultats)} heure(s) analysee(s), {total} alerte(s) detectee(s)/mise(s) a jour ===")
    for r in resultats:
        if r["alertes_detectees"] > 0:
            print(f"    {r['bucket']}  alertes={r['alertes_detectees']}")


if __name__ == "__main__":
    main()