"""DetecteurAnomalie - Etapes 4 et 5 du document technique (SS4.4, SSV).

Etape 4 : pour un jour d et une heure k donnes, calcule la part de trafic reelle de chaque forfait : alpha^k_d,j = volume(j, bucket) / SUM(volume(*, bucket)).

Etape 5 : compare alpha^k_d,j au profil historique (Vk_j +/- sigma^k_j, matrice D/sigma de profil_trafic_snapshot) via la regle empirique. Un forfait est declare SURACTIF si :
    alpha^k_d,j > Vk_j + 3 * sigma^k_j     (niveau 2 - anomalie forte, seuil 3-sigma)
Niveau 1 (surveillance) si le depassement n'est qu'a 2-sigma :
    Vk_j + 2*sigma^k_j < alpha^k_d,j <= Vk_j + 3*sigma^k_j

Les forfaits sans profil de reference disponible (sigma_val NULL - un seul jour d'historique, cf. profil_trafic_snapshot) sont exclus de la detection (pas
assez de donnees pour juger une deviation), pas traites comme non-anomaux par defaut - distinction importante, jamais de faux "normal" faute de donnees.

Ecrit dans evenement_detecte (type_evenement='ANOMALIE', niveau 1 ou 2, score=alpha observe).
"""
from __future__ import annotations

import argparse
import datetime as dt
from typing import Optional


class DetecteurAnomalie:
    """Detecte les forfaits SURACTIFS pour un jour donne, heure par heure, contre un profil de reference."""

    def detecter_heure(self, bucket: dt.datetime, bucket_ref: dt.datetime) -> dict:
        """Calcule alpha observe pour l'heure `bucket`, le compare au profil `bucket_ref` (profil_trafic_snapshot), et upsert les anomalies detectees dans evenement_detecte."""
        from src.donnees.db import connexion

        bucket = bucket.replace(minute=0, second=0, microsecond=0)
        bucket_ref = bucket_ref.replace(hour=0, minute=0, second=0, microsecond=0)
        heure = bucket.hour

        with connexion() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH volumes AS (
                        SELECT forfait_id,
                                (volume_voix_fr + volume_voix_hr + volume_data) AS volume
                        FROM trafic_horaire
                        WHERE bucket = %s
                    ),
                    total AS (
                        SELECT SUM(volume) AS total FROM volumes
                    ),
                    alphas AS (
                        SELECT v.forfait_id,
                                CASE WHEN t.total > 0 THEN v.volume / t.total ELSE 0 END AS alpha
                        FROM volumes v, total t
                    ),
                    compare AS (
                        SELECT a.forfait_id, a.alpha,
                                p.d_val, p.sigma_val,
                                (p.d_val + 3 * p.sigma_val) AS seuil_3s,
                                (p.d_val + 2 * p.sigma_val) AS seuil_2s
                        FROM alphas a
                        JOIN profil_trafic_snapshot p
                        ON p.forfait_id = a.forfait_id
                        AND p.bucket_ref = %s
                        AND p.heure = %s
                        WHERE p.sigma_val IS NOT NULL  -- pas assez d'historique -> exclu, pas suppose normal
                    )
                    INSERT INTO evenement_detecte (bucket, type_evenement, forfait_id, score, niveau)
                    SELECT
                        %s, 'ANOMALIE', forfait_id, alpha,
                        CASE WHEN alpha > seuil_3s THEN 2
                            WHEN alpha > seuil_2s THEN 1
                            ELSE NULL END
                    FROM compare
                    WHERE alpha > seuil_2s
                    ON CONFLICT (bucket, type_evenement, COALESCE(forfait_id, ''), COALESCE(indicateur, ''))
                    DO UPDATE SET score = EXCLUDED.score, niveau = EXCLUDED.niveau
                    """,
                    (bucket, bucket_ref, heure, bucket),
                )
                n = cur.rowcount
        return {"bucket": bucket.isoformat(), "anomalies_detectees": n}

    def detecter_periode(self, debut: dt.datetime, fin: dt.datetime, bucket_ref: dt.datetime) -> list[dict]:
        """Boucle detecter_heure() sur chaque heure de [debut, fin[, contre le meme profil bucket_ref."""
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
    p = argparse.ArgumentParser(
        description="Detecte les forfaits SURACTIFS (regle 3-sigma, Etapes 4-5) sur une periode, contre un profil de reference (profil_trafic_snapshot) donne."
    )
    p.add_argument("--debut", required=True, help="Date/heure de debut ISO")
    p.add_argument("--fin", required=True, help="Date/heure de fin ISO, exclusive")
    p.add_argument("--bucket-ref", required=True, help="Jour de reference du profil a utiliser, ISO (ex. 2025-08-02)")
    args = p.parse_args(argv)

    detecteur = DetecteurAnomalie()
    resultats = detecteur.detecter_periode(_parse_date(args.debut), _parse_date(args.fin), _parse_date(args.bucket_ref))

    total = sum(r["anomalies_detectees"] for r in resultats)
    print(f"=== {len(resultats)} heure(s) analysee(s), {total} anomalie(s) detectee(s)/mise(s) a jour ===")
    for r in resultats:
        if r["anomalies_detectees"] > 0:
            print(f"    {r['bucket']}  anomalies={r['anomalies_detectees']}")


if __name__ == "__main__":
    main()