"""AgregateurTrafic - agrege consommation_forfait vers trafic_horaire (SUM voix/data par bucket horaire + forfait_id).

Regles retenues :
  * cdr_type='voiceCall' -> voix : appels_voix_fr += 1, volume_voix_fr += duration (secondes) par ligne.
    Seule valeur de cdr_type confirmee a ce jour pour la voix (cf. commentaire consommation_forfait.cdr_type dans schema.sql).
  * tout autre cdr_type non vide -> data (par elimination) : volume_data += data_volume. Meme convention que CollecteurBilling, qui
    traite deja tout cdrType != "forfait" comme une consommation par defaut - la ou/les valeur(s) exactes cote data restent a confirmer
    mais cela ne bloque pas cette agregation, qui n'a pas besoin de connaitre la valeur exacte, seulement qu'elle n'est pas "voiceCall".
  * forfait_id IS NULL exclu de l'agregation (debit direct du solde principal, hors decomposition par forfait - cf. commentaire sur
    consommation_forfait.forfait_id dans schema.sql). Ces lignes restent en base pour tracabilite, simplement pas comptees ici.
  * appels_voix_hr / volume_voix_hr restent a 0 : consommation_forfait (source SurePay/billing) ne porte aucun indicateur half-rate - cette distinction
    n'existe que cote reseau (enregistrement_cdr.half_rate), pas cote billing. Limitation deja documentee dans schema.sql ("Alimentee par agregation
    de consommation_forfait ... en attendant le branchement direct de TRAFSCAN") - a corriger quand TRAFSCAN sera branche directement.

Prerequis avant d'executer ce module : la table `forfait` doit deja contenir les codes qui apparaitront dans consommation_forfait.forfait_id (FK).
consommation_forfait.forfait_id est lui-meme une FK vers forfait(id) : si un code de forfait rencontre dans les echantillons SurePay n'existe pas encore dans `forfait`,
c'est CollecteurBilling qui echouera a l'insertion (bien avant ce module) - schema.sql ne seme actuellement que operateur et qci_caracteristique, pas forfait.
"""
from __future__ import annotations

import argparse
import datetime as dt
from typing import Optional


class AgregateurTrafic:
    """Agrege consommation_forfait -> trafic_horaire, une heure a la fois."""

    def agreger(self, bucket: dt.datetime) -> dict:
        """Agrege l'heure `bucket` : upsert une ligne trafic_horaire par forfait_id concerne (0 a n lignes selon le nombre de forfaits distincts actifs sur l'heure)."""
        from src.donnees.db import connexion  # import tardif, meme pattern que les autres modules

        bucket = bucket.replace(minute=0, second=0, microsecond=0)
        fin = bucket + dt.timedelta(hours=1)

        with connexion() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO trafic_horaire
                        (bucket, forfait_id, appels_voix_fr, volume_voix_fr, appels_voix_hr, volume_voix_hr, volume_data, est_mock)
                    SELECT
                        %s AS bucket,
                        forfait_id,
                        SUM(CASE WHEN cdr_type = 'voiceCall' THEN 1 ELSE 0 END)                         AS appels_voix_fr,
                        SUM(CASE WHEN cdr_type = 'voiceCall' THEN COALESCE(duration, 0) ELSE 0 END)     AS volume_voix_fr,
                        0                                                                               AS appels_voix_hr,
                        0                                                                               AS volume_voix_hr,
                        SUM(CASE WHEN cdr_type <> 'voiceCall' THEN COALESCE(data_volume, 0) ELSE 0 END) AS volume_data,
                        bool_or(est_mock)                                                               AS est_mock
                    FROM consommation_forfait
                    WHERE forfait_id IS NOT NULL
                    AND bucket >= %s AND bucket < %s
                    GROUP BY forfait_id
                    ON CONFLICT (bucket, forfait_id) DO UPDATE SET
                        appels_voix_fr = EXCLUDED.appels_voix_fr,
                        volume_voix_fr = EXCLUDED.volume_voix_fr,
                        appels_voix_hr = EXCLUDED.appels_voix_hr,
                        volume_voix_hr = EXCLUDED.volume_voix_hr,
                        volume_data    = EXCLUDED.volume_data,
                        est_mock       = EXCLUDED.est_mock
                    """,
                    (bucket, bucket, fin),
                )
                n = cur.rowcount
        return {"bucket": bucket.isoformat(), "lignes_forfait": n}

    def agreger_periode(self, debut: dt.datetime, fin: dt.datetime) -> list[dict]:
        """Boucle agreger() sur chaque heure de [debut, fin[."""
        resultats = []
        bucket = debut.replace(minute=0, second=0, microsecond=0)
        while bucket < fin:
            resultats.append(self.agreger(bucket))
            bucket += dt.timedelta(hours=1)
        return resultats


def _parse_date(valeur: str) -> dt.datetime:
    """Meme convention que les autres modules : ISO, UTC si pas de fuseau explicite."""
    naive_ou_aware = dt.datetime.fromisoformat(valeur)
    if naive_ou_aware.tzinfo is None:
        return naive_ou_aware.replace(tzinfo=dt.timezone.utc)
    return naive_ou_aware


def main(argv: Optional[list[str]] = None) -> None:
    p = argparse.ArgumentParser(
        description="Agrege consommation_forfait vers trafic_horaire (SUM voix/data par bucket+forfait), sur une periode donnee, heure par heure."
    )
    p.add_argument("--debut", required=True, help="Date/heure de debut ISO (ex. 2025-08-01T17:00:00)")
    p.add_argument("--fin", required=True, help="Date/heure de fin ISO, exclusive")
    args = p.parse_args(argv)

    agregateur = AgregateurTrafic()
    resultats = agregateur.agreger_periode(_parse_date(args.debut), _parse_date(args.fin))

    total_lignes = sum(r["lignes_forfait"] for r in resultats)
    print(f"=== {len(resultats)} heure(s) agregee(s), {total_lignes} ligne(s) trafic_horaire ecrite(s) au total ===")
    for r in resultats[:5]:
        print(f"    {r['bucket']}  forfaits_distincts={r['lignes_forfait']}")
    if len(resultats) > 5:
        print(f"    ... ({len(resultats) - 5} de plus)")


if __name__ == "__main__":
    main()