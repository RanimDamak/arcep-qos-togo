"""AnalyseurCorrelation - Etape 7 du document technique (SSV). Pour chaque (forfait, indicateur) co-occurrents (ANOMALIE + ALERTE_QOS
a la meme heure), calcule une VRAIE correlation de Pearson (CORR(), agregat natif Postgres) entre le volume trafic du forfait et
l'indicateur QoS observe, sur l'historique disponible a la meme heure-du-jour (pas seulement l'heure courante).

Volontairement PAS de placeholder/mock ici : contrairement au catalogue forfait ou aux CDR reels (donnees externes manquantes), le
manque de profondeur historique se resout naturellement avec le temps en prod (90j glissants) - donc le calcul reel est ecrit des
maintenant, garde par un seuil minimal d'echantillon (MIN_POINTS) plutot que remplace par une logique jetable a reecrire plus tard.

Sous N points, la paire est ignoree (pas assez d'historique pour juger), jamais traitee comme "non correlee" par defaut - meme principe que sigma_val NULL ailleurs dans le projet.

est_causal = TRUE si |r| >= SEUIL_CAUSAL (config par defaut : 0.7, a ajuster avec l'equipe/ARCEP).
"""
from __future__ import annotations

import argparse
import datetime as dt
from typing import Optional

MIN_POINTS = 3       # nombre minimum de jours (a la meme heure) pour calculer un r significatif
SEUIL_CAUSAL = 0.7   # |r| au-dela duquel on declare une relation causale probable

_COL_INDICATEUR = {
    "C": "congestion",
    "QoS": "qos",
    "HR": "half_rate",
}


class AnalyseurCorrelation:
    """Calcule les correlations trafic-forfait / indicateur-QoS pour les co-occurrences ANOMALIE+ALERTE_QOS."""

    def analyser_heure(
        self,
        bucket: dt.datetime,
        fenetre_debut: dt.datetime,
        fenetre_fin: dt.datetime,
    ) -> dict:
        """Pour l'heure `bucket` : trouve les paires (forfait ANOMALIE, indicateur ALERTE_QOS) co-occurrentes, calcule leur correlation
        sur [fenetre_debut, fenetre_fin[ a la meme heure-du-jour, et upsert les resultats significatifs dans evenement_detecte."""
        from src.donnees.db import connexion

        bucket = bucket.replace(minute=0, second=0, microsecond=0)
        heure = bucket.hour

        with connexion() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT forfait_id FROM evenement_detecte
                    WHERE bucket = %s AND type_evenement = 'ANOMALIE' AND niveau IS NOT NULL
                    """,
                    (bucket,),
                )
                forfaits_anomaux = [r[0] for r in cur.fetchall()]

                cur.execute(
                    """
                    SELECT indicateur FROM evenement_detecte
                    WHERE bucket = %s AND type_evenement = 'ALERTE_QOS'
                    """,
                    (bucket,),
                )
                indicateurs_alertes = [r[0] for r in cur.fetchall()]

                n_ecrites = 0
                for forfait_id in forfaits_anomaux:
                    for indicateur in indicateurs_alertes:
                        col_qos = _COL_INDICATEUR[indicateur]
                        cur.execute(
                            f"""
                            WITH paires AS (
                                SELECT t.bucket,
                                        (t.volume_voix_fr + t.volume_voix_hr + t.volume_data) AS volume,
                                        o.{col_qos} AS indicateur_val
                                FROM trafic_horaire t
                                JOIN observation_horaire o ON o.bucket = t.bucket
                                WHERE t.forfait_id = %s
                                    AND EXTRACT(HOUR FROM t.bucket) = %s
                                    AND t.bucket >= %s AND t.bucket < %s
                                    AND o.{col_qos} IS NOT NULL
                            )
                            SELECT CORR(volume, indicateur_val), count(*)
                            FROM paires
                            """,
                            (forfait_id, heure, fenetre_debut, fenetre_fin),
                        )
                        r, n = cur.fetchone()
                        if r is None or n < MIN_POINTS:
                            continue  # historique insuffisant -> ignore, jamais "non correle" par defaut

                        cur.execute(
                            """
                            INSERT INTO evenement_detecte
                                (bucket, type_evenement, forfait_id, indicateur, score, est_causal)
                            VALUES (%s, 'CORRELATION', %s, %s, %s, %s)
                            ON CONFLICT (bucket, type_evenement, COALESCE(forfait_id, ''), COALESCE(indicateur, ''))
                            DO UPDATE SET score = EXCLUDED.score, est_causal = EXCLUDED.est_causal
                            """,
                            (bucket, forfait_id, indicateur, r, abs(r) >= SEUIL_CAUSAL),
                        )
                        n_ecrites += 1
        return {"bucket": bucket.isoformat(), "correlations_calculees": n_ecrites}

    def analyser_periode(
        self, debut: dt.datetime, fin: dt.datetime, fenetre_debut: dt.datetime, fenetre_fin: dt.datetime
    ) -> list[dict]:
        resultats = []
        bucket = debut.replace(minute=0, second=0, microsecond=0)
        while bucket < fin:
            resultats.append(self.analyser_heure(bucket, fenetre_debut, fenetre_fin))
            bucket += dt.timedelta(hours=1)
        return resultats


def _parse_date(valeur: str) -> dt.datetime:
    naive_ou_aware = dt.datetime.fromisoformat(valeur)
    if naive_ou_aware.tzinfo is None:
        return naive_ou_aware.replace(tzinfo=dt.timezone.utc)
    return naive_ou_aware


def main(argv: Optional[list[str]] = None) -> None:
    p = argparse.ArgumentParser(
        description="Calcule les correlations (Pearson, CORR()) entre forfaits ANOMALIE et indicateurs ALERTE_QOS co-occurrents, sur une fenetre historique donnee."
    )
    p.add_argument("--debut", required=True)
    p.add_argument("--fin", required=True)
    p.add_argument("--fenetre-debut", required=True, help="Debut de l'historique pour le calcul de CORR()")
    p.add_argument("--fenetre-fin", required=True, help="Fin de l'historique pour le calcul de CORR(), exclusive")
    args = p.parse_args(argv)

    analyseur = AnalyseurCorrelation()
    resultats = analyseur.analyser_periode(
        _parse_date(args.debut), _parse_date(args.fin),
        _parse_date(args.fenetre_debut), _parse_date(args.fenetre_fin),
    )
    total = sum(r["correlations_calculees"] for r in resultats)
    print(f"=== {len(resultats)} heure(s) analysee(s), {total} correlation(s) calculee(s) ===")
    for r in resultats:
        if r["correlations_calculees"] > 0:
            print(f"    {r['bucket']}  correlations={r['correlations_calculees']}")


if __name__ == "__main__":
    main()