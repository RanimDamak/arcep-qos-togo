"""AgregateurHoraire - calcule le cycle d'agregation horaire : lit les tables brutes et ecrit/met a jour la ligne correspondante dans `observation_horaire` (upsert sur bucket).

Perimetre de ce module : congestion et qos uniquement. half_rate et alpha_json (decomposition par forfait) restent hors perimetre ici -
a implementer separement, l'upsert ne touche donc jamais ces deux colonnes pour ne pas ecraser une valeur deja calculee ailleurs.

--- Regle congestion (observation_horaire.congestion) ---
Moyenne simple des 3 lignes congestion_horaire de l'heure (CS/PS/EPS - reseau entier, pas de cellule a agreger). Fraction 0-1 (pas un pourcentage). Le detail par
domaine reste disponible via requete directe sur congestion_horaire si besoin d'analyse fine. Colonne NOT NULL en base : si aucune ligne n'existe pour l'heure
(ex. QUALCOP/mock pas encore lance sur cette periode), on ecrit 0.0 plutot que de bloquer l'insertion - a surveiller si ce cas devient frequent en prod.

--- Regle QoS (observation_horaire.qos) ---
Vecteur d'indicateurs individuels par CDR, somme directe (pas de ratio). L'indicateur retenu par CDR est le packet_error_loss_rate (PELR) de son QCI negocie, via jointure
sur qci_caracteristique (donnees normalisees TS 23.203, validees dans Elements_QoS_final.docx) - plutot qu'un seuil de "degradation" invente et non confirme. qos_qci est
actuellement NULL sur les echantillons reels (champ absent du JSON source) : utiliser --mock sur CollecteurCDR pour tester cette agregation en attendant que l'equipe
ajoute le champ QoS au flux JSON/CSV de prod. Colonne NULLABLE : si aucun CDR qualifiant sur l'heure, qos reste NULL (pas de donnee, distinct de "zero degradation").
"""
from __future__ import annotations

import argparse
import datetime as dt
from typing import Optional


class AgregateurHoraire:
    """Lit congestion_horaire / enregistrement_cdr pour une heure donnee et met a jour observation_horaire."""

    def _agreger_congestion(self, cur, bucket: dt.datetime) -> float:
        """Moyenne simple des 3 domaines (CS/PS/EPS, reseau entier). 0.0 si aucune ligne (colonne NOT NULL)."""
        cur.execute(
            "SELECT AVG(valeur) FROM congestion_horaire WHERE bucket = %s",
            (bucket,),
        )
        row = cur.fetchone()
        valeur = row[0] if row else None
        return float(valeur) if valeur is not None else 0.0

    def _agreger_qos(self, cur, bucket: dt.datetime) -> Optional[float]:
        """Somme directe des PELR (via qci_caracteristique) des CDR portant une QoS sur l'heure. NULL si aucun CDR qualifiant (qos_qci NULL sur tous, ou aucun CDR sur la periode)."""
        cur.execute(
            """
            SELECT SUM(q.packet_error_loss_rate::float8)
            FROM enregistrement_cdr c
            JOIN qci_caracteristique q ON q.qci = c.qos_qci
            WHERE c.cdr_timestamp >= %s AND c.cdr_timestamp < %s
            """,
            (bucket, bucket + dt.timedelta(hours=1)),
        )
        row = cur.fetchone()
        return float(row[0]) if row and row[0] is not None else None

    def agreger(self, bucket: dt.datetime) -> dict:
        """Calcule congestion + qos pour l'heure `bucket` et upsert dans observation_horaire. Ne touche jamais half_rate / alpha_json (hors perimetre, calcules ailleurs)."""
        from src.donnees.db import connexion  # import tardif, meme pattern que les collecteurs

        bucket = bucket.replace(minute=0, second=0, microsecond=0)
        with connexion() as conn:
            with conn.cursor() as cur:
                congestion = self._agreger_congestion(cur, bucket)
                qos = self._agreger_qos(cur, bucket)
                cur.execute(
                    """
                    INSERT INTO observation_horaire (bucket, congestion, qos)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (bucket) DO UPDATE SET
                        congestion = EXCLUDED.congestion,
                        qos        = EXCLUDED.qos
                    """,
                    (bucket, congestion, qos),
                )
        return {"bucket": bucket.isoformat(), "congestion": congestion, "qos": qos}

    def agreger_periode(self, debut: dt.datetime, fin: dt.datetime) -> list[dict]:
        """Boucle agreger() sur chaque heure de [debut, fin[."""
        resultats = []
        bucket = debut.replace(minute=0, second=0, microsecond=0)
        while bucket < fin:
            resultats.append(self.agreger(bucket))
            bucket += dt.timedelta(hours=1)
        return resultats


def _parse_date(valeur: str) -> dt.datetime:
    """Meme convention que les collecteurs : ISO, UTC si pas de fuseau explicite."""
    naive_ou_aware = dt.datetime.fromisoformat(valeur)
    if naive_ou_aware.tzinfo is None:
        return naive_ou_aware.replace(tzinfo=dt.timezone.utc)
    return naive_ou_aware


def main(argv: Optional[list[str]] = None) -> None:
    p = argparse.ArgumentParser(
        description="Agrege congestion_horaire + enregistrement_cdr vers observation_horaire (congestion, qos) sur une periode donnee, heure par heure."
    )
    p.add_argument("--debut", required=True, help="Date/heure de debut ISO (ex. 2026-07-01T00:00:00)")
    p.add_argument("--fin", required=True, help="Date/heure de fin ISO, exclusive")
    args = p.parse_args(argv)

    agregateur = AgregateurHoraire()
    resultats = agregateur.agreger_periode(_parse_date(args.debut), _parse_date(args.fin))

    n_qos_nulle = sum(1 for r in resultats if r["qos"] is None)
    print(f"=== {len(resultats)} heure(s) agregee(s) ===")
    print(f"    qos NULL (aucun CDR avec QoS sur l'heure) : {n_qos_nulle}/{len(resultats)}")
    for r in resultats[:5]:
        print(f"    {r['bucket']}  congestion={r['congestion']:.3f}  qos={r['qos']}")
    if len(resultats) > 5:
        print(f"    ... ({len(resultats) - 5} de plus)")


if __name__ == "__main__":
    main()