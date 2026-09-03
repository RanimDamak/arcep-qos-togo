"""ModuleLissage (volet QoS/congestion) - calcule le profil statistique de reference profil_qos_snapshot
a partir de observation_horaire, sur une fenetre [debut, fin[ de longueur libre (2 jours, 90 jours, 2 ans -
peu importe, c'est l'appelant qui choisit, rien n'est fige en dur ici). Cf. Plan_Projet_ARCEP_v2.docx
Jour 4 et Architecture_BDD_v4.docx (famille 4).

Pour chaque heure-du-jour (0-23), calcule la moyenne et l'ecart-type (echantillon, STDDEV_SAMP) de
congestion / qos / half_rate sur toutes les lignes observation_horaire de la fenetre, et ecrit une ligne
profil_qos_snapshot par heure pour le bucket_ref donne (upsert sur (bucket_ref, heure)).

Volet trafic (profil_trafic_snapshot, par forfait) HORS PERIMETRE ici : bloque tant que trafic_horaire
n'est pas peuple (cf. point ouvert sur le seed de la table forfait). A faire separement une fois debloque.

NULL : AVG/STDDEV_SAMP ignorent nativement les valeurs NULL (ex. half_rate, jamais calcule pour
l'instant - toutes les lignes observation_horaire l'ont a NULL) - le profil sortira donc avec
hr_moy/hr_sigma = NULL tant que half_rate n'est pas alimente, sans planter.
"""
from __future__ import annotations

import argparse
import datetime as dt
from typing import Optional


class ModuleLissage:
    """Calcule profil_qos_snapshot a partir de observation_horaire sur une fenetre arbitraire."""

    def calculer_profil_trafic(
        self,
        bucket_ref: dt.datetime,
        debut_fenetre: dt.datetime,
        fin_fenetre: dt.datetime,
    ) -> dict:
        """Calcule le profil trafic en PART DE TRAFIC (alpha), pas en volume brut - cf. document technique SS4.2/4.3 :
        alpha^k_i,j = volume du forfait j / volume total (tous forfaits) a l'heure.
        d_val/sigma_val = moyenne/ecart-type de alpha sur la fenetre, par (forfait_id, heure-du-jour)."""
        from src.donnees.db import connexion

        bucket_ref = bucket_ref.replace(hour=0, minute=0, second=0, microsecond=0)

        with connexion() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH volumes AS (
                        SELECT bucket, forfait_id,
                            (volume_voix_fr + volume_voix_hr + volume_data) AS volume
                        FROM trafic_horaire
                        WHERE bucket >= %s AND bucket < %s
                    ),
                    totaux AS (
                        SELECT bucket, SUM(volume) AS total
                        FROM volumes
                        GROUP BY bucket
                    ),
                    alphas AS (
                        SELECT v.bucket, v.forfait_id,
                            CASE WHEN t.total > 0 THEN v.volume / t.total ELSE 0 END AS alpha
                        FROM volumes v
                        JOIN totaux t ON t.bucket = v.bucket
                    )
                    INSERT INTO profil_trafic_snapshot
                        (bucket_ref, forfait_id, heure, d_val, sigma_val)
                    SELECT
                        %s AS bucket_ref,
                        forfait_id,
                        EXTRACT(HOUR FROM bucket)::smallint AS heure,
                        AVG(alpha)         AS d_val,
                        STDDEV_SAMP(alpha) AS sigma_val
                    FROM alphas
                    GROUP BY forfait_id, EXTRACT(HOUR FROM bucket)
                    ON CONFLICT (bucket_ref, forfait_id, heure) DO UPDATE SET
                        d_val     = EXCLUDED.d_val,
                        sigma_val = EXCLUDED.sigma_val
                    """,
                    (debut_fenetre, fin_fenetre, bucket_ref),
                )
                n = cur.rowcount
        return {
            "bucket_ref": bucket_ref.isoformat(),
            "fenetre": f"{debut_fenetre.isoformat()} -> {fin_fenetre.isoformat()}",
            "lignes_calculees": n,
        }
    
    def calculer_profil_qos(
        self,
        bucket_ref: dt.datetime,
        debut_fenetre: dt.datetime,
        fin_fenetre: dt.datetime,
    ) -> dict:
        """Calcule le profil (24 lignes, une par heure-du-jour presente dans la fenetre) et upsert
        dans profil_qos_snapshot pour le jour de reference bucket_ref."""
        from src.donnees.db import connexion  # import tardif, meme pattern que les autres modules

        bucket_ref = bucket_ref.replace(hour=0, minute=0, second=0, microsecond=0)

        with connexion() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO profil_qos_snapshot
                        (bucket_ref, heure, c_moy, c_sigma, qos_moy, qos_sigma, hr_moy, hr_sigma)
                    SELECT
                        %s AS bucket_ref,
                        EXTRACT(HOUR FROM bucket)::smallint AS heure,
                        AVG(congestion), STDDEV_SAMP(congestion),
                        AVG(qos),        STDDEV_SAMP(qos),
                        AVG(half_rate),  STDDEV_SAMP(half_rate)
                    FROM observation_horaire
                    WHERE bucket >= %s AND bucket < %s
                    GROUP BY EXTRACT(HOUR FROM bucket)
                    ON CONFLICT (bucket_ref, heure) DO UPDATE SET
                        c_moy     = EXCLUDED.c_moy,
                        c_sigma   = EXCLUDED.c_sigma,
                        qos_moy   = EXCLUDED.qos_moy,
                        qos_sigma = EXCLUDED.qos_sigma,
                        hr_moy    = EXCLUDED.hr_moy,
                        hr_sigma  = EXCLUDED.hr_sigma
                    """,
                    (bucket_ref, debut_fenetre, fin_fenetre),
                )
                n = cur.rowcount
        return {
            "bucket_ref": bucket_ref.isoformat(),
            "fenetre": f"{debut_fenetre.isoformat()} -> {fin_fenetre.isoformat()}",
            "heures_calculees": n,
        }


def _parse_date(valeur: str) -> dt.datetime:
    """Meme convention que les autres modules : ISO, UTC si pas de fuseau explicite."""
    naive_ou_aware = dt.datetime.fromisoformat(valeur)
    if naive_ou_aware.tzinfo is None:
        return naive_ou_aware.replace(tzinfo=dt.timezone.utc)
    return naive_ou_aware


def main(argv: Optional[list[str]] = None) -> None:
    p = argparse.ArgumentParser(
        description="Calcule profil_qos_snapshot (moyenne/ecart-type par heure-du-jour) a partir de "
                    "observation_horaire. Usage normal (job recurrent) : --fenetre-jours N, bucket_ref "
                    "= aujourd'hui par defaut. Usage backtest/test : --bucket-ref, --fenetre-debut, "
                    "--fenetre-fin explicites pour rejouer une date passee."
    )
    p.add_argument("--fenetre-jours", type=int,
                    help="Longueur de la fenetre en jours, comptee en arriere depuis bucket-ref "
                        "(ex. 90 = les 90 jours precedents). Ignore si --fenetre-debut/--fenetre-fin "
                        "sont fournis explicitement.")
    p.add_argument("--bucket-ref", help="Jour de reference du profil, ISO (ex. 2025-08-02). "
                    "Par defaut : aujourd'hui.")
    p.add_argument("--fenetre-debut", help="Debut de la fenetre, ISO - remplace --fenetre-jours si fourni.")
    p.add_argument("--fenetre-fin", help="Fin de la fenetre, ISO, exclusive - remplace bucket-ref comme "
                    "borne haute si fourni.")
    args = p.parse_args(argv)

    bucket_ref = _parse_date(args.bucket_ref) if args.bucket_ref else dt.datetime.now(dt.timezone.utc)
    bucket_ref = bucket_ref.replace(hour=0, minute=0, second=0, microsecond=0)

    if args.fenetre_debut and args.fenetre_fin:
        fenetre_debut = _parse_date(args.fenetre_debut)
        fenetre_fin = _parse_date(args.fenetre_fin)
    elif args.fenetre_jours:
        fenetre_fin = bucket_ref
        fenetre_debut = bucket_ref - dt.timedelta(days=args.fenetre_jours)
    else:
        p.error("fournir soit --fenetre-jours, soit --fenetre-debut et --fenetre-fin.")

    module = ModuleLissage()
    resultat = module.calculer_profil_qos(bucket_ref, fenetre_debut, fenetre_fin)
    print(
        f"[profil_qos_snapshot] bucket_ref={resultat['bucket_ref']} "
        f"fenetre={resultat['fenetre']} heures_calculees={resultat['heures_calculees']}"
    )
    resultat_trafic = module.calculer_profil_trafic(bucket_ref, fenetre_debut, fenetre_fin)
    print(
        f"[profil_trafic_snapshot] bucket_ref={resultat_trafic['bucket_ref']} "
        f"fenetre={resultat_trafic['fenetre']} lignes_calculees={resultat_trafic['lignes_calculees']}"
    )


if __name__ == "__main__":
    main()