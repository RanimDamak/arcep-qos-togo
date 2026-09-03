"""Ingestion par dossier - parcourt une arborescence de fichiers CDR/billing et dispatche chaque fichier vers le bon
collecteur selon son sous-dossier, au lieu de lancer chaque fichier à la main (cf. Plan_Projet_ARCEP_v2.docx, Jour 1).

Structure attendue (celle du dataset ARCEP_Forfaits_QoS/CDR_JSON_ARCEP_TOGO) :

    <racine>/
    ├── Moov MSC/    *.json   -> CollecteurCDR (réseau, voix MOOV)
    ├── PGW/         *.json   -> CollecteurCDR (réseau, data)
    └── SurePay/     *.json   -> CollecteurBilling (billing, achats + consommations)

Le rattachement dossier -> collecteur se fait par mot-clé insensible à la casse dans le nom du dossier ("msc" ou "pgw" -> réseau ;
"surepay" -> billing), pas par un nom exact, pour rester tolérant à de petites variations de nommage.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from src.collecte.collecteur_cdr import CollecteurCDR
from src.collecte.collecteur_billing import CollecteurBilling

# Extensions reconnues par collecteur. Tout le reste (ex. .dat Nokia MSS) est signalé, pas parsé.
EXT_RESEAU = {".json"}
EXT_BILLING = {".json"}


def _type_dossier(nom_dossier: str) -> Optional[str]:
    """Détermine le type de collecteur à partir du nom du sous-dossier. Rend 'reseau',
    'billing', ou None si non reconnu."""
    n = nom_dossier.lower()
    if "surepay" in n:
        return "billing"
    if "msc" in n or "pgw" in n:
        return "reseau"
    return None


def ingerer_dossier(racine: str | Path, mock: bool = False) -> dict:
    """Parcourt racine/*/*.* et dispatche chaque fichier vers le bon collecteur.
    Retourne un récapitulatif global {fichiers_traites, fichiers_ignores, details: [...]}."""
    racine = Path(racine)
    if not racine.is_dir():
        raise NotADirectoryError(f"{racine} n'est pas un dossier.")

    collecteur_cdr = CollecteurCDR(mock_qos=mock)
    collecteur_billing = CollecteurBilling()

    details = []
    fichiers_traites = 0
    fichiers_ignores = 0

    for sous_dossier in sorted(p for p in racine.iterdir() if p.is_dir()):
        type_dossier = _type_dossier(sous_dossier.name)

        if type_dossier is None:
            print(f"[SKIP dossier] {sous_dossier.name} : type non reconnu (ni MSC/PGW, ni SurePay)")
            continue

        for fichier in sorted(sous_dossier.iterdir()):
            if not fichier.is_file():
                continue

            if type_dossier == "reseau":
                if fichier.suffix.lower() not in EXT_RESEAU:
                    print(f"[SKIP fichier] {sous_dossier.name}/{fichier.name} : extension "
                            f"'{fichier.suffix}' non supportée par CollecteurCDR).")
                    fichiers_ignores += 1
                    continue
                resume = collecteur_cdr.parser_fichier(fichier)
                resume["dossier"] = sous_dossier.name
                resume["collecteur"] = "CollecteurCDR"
                print(f"[{sous_dossier.name}/{resume['fichier']}] lus={resume['lus']} "
                        f"insérés={resume['inseres']} ignorés={resume['ignores']} "
                        f"mock_qos={resume['mock_qos']}")

            else:  # billing
                if fichier.suffix.lower() not in EXT_BILLING:
                    print(f"[SKIP fichier] {sous_dossier.name}/{fichier.name} : extension "
                            f"'{fichier.suffix}' non supportée par CollecteurBilling.")
                    fichiers_ignores += 1
                    continue
                resume = collecteur_billing.parser_fichier(fichier)
                resume["dossier"] = sous_dossier.name
                resume["collecteur"] = "CollecteurBilling"
                print(f"[{sous_dossier.name}/{resume['fichier']}] lus={resume['lus']} "
                        f"insérés={resume['inseres']} achats_ignorés={resume['achats']} "
                        f"ignorés={resume['ignores']}")

            details.append(resume)
            fichiers_traites += 1

    return {"fichiers_traites": fichiers_traites, "fichiers_ignores": fichiers_ignores,
            "details": details}


def main(argv: Optional[list[str]] = None) -> None:
    p = argparse.ArgumentParser(
        description="Ingestion par dossier : dispatche chaque fichier CDR/billing vers le bon "
                    "collecteur selon son sous-dossier (Moov MSC / PGW / Yas MSC / SurePay).")
    p.add_argument("dossier", help="Dossier racine (ex. .../CDR_JSON_ARCEP_TOGO)")
    p.add_argument("--mock", action="store_true",
                    help="Remplit des QoS FICTIVES pour les CDR réseau (mêmes règles que CollecteurCDR).")
    args = p.parse_args(argv)

    resume = ingerer_dossier(args.dossier, mock=args.mock)

    print()
    print(f"=== Total : {resume['fichiers_traites']} fichier(s) traité(s), "
            f"{resume['fichiers_ignores']} ignoré(s) (extension non supportée) ===")
    total_lus = sum(d["lus"] for d in resume["details"])
    total_inseres = sum(d["inseres"] for d in resume["details"])
    print(f"    lignes lues au total    : {total_lus}")
    print(f"    lignes insérées au total: {total_inseres}")


if __name__ == "__main__":
    main()