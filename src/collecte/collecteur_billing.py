"""CollecteurBilling - lit un fichier JSON SurePay (billing) et insère les lignes de CONSOMMATION dans `consommation_forfait` (TimescaleDB). Les événements d'ACHAT
(cdrType="forfait") sont repérés et volontairement ignorés : on ne stocke pas montant/solde/quota, l'algorithme n'en a pas besoin (cf. Architecture_BDD.docx).

Structure réelle des fichiers JSON SurePay (vérifiée sur les 4 échantillons reçus) :
un objet unique par événement (pas de wrapper "templates" comme les CDR réseau) :
{ "_index": ..., "_id": ..., "_source": { ...31 champs... }, "fields": {...} }

2 types d'événements identifiés dans "_source.cdrType" :
  * "forfait"   -> ACHAT/activation d'un forfait. typeForfait = nom commercial (ex. "Lema200+").
                    montant/beforeBalance/afterBalance renseignés, duration=0, dataVolume=0.
                    -> IGNORÉ (pas stocké, cf. note ci-dessus).
  * "voiceCall" -> CONSOMMATION voix. typeForfait = code numérique (ex. "892"), duration != 0.
                    Autres cdrType possibles côté data : PAS ENCORE CONFIRMÉS (point ouvert,
                    voir Plan_Projet_ARCEP_v2.docx décision n°4). Tout cdrType != "forfait" est
                    donc traité comme une consommation par défaut, quel que soit son nom exact.

Champs utiles : cdrType, dateTime, servedIsdn, typeForfait, duration, dataVolume.

Point important : typeForfait peut être vide "" sur un événement de consommation -> l'abonné a été débité directement du solde principal, pas d'un forfait. On garde la ligne
(traçabilité) mais forfait_id reste NULL (colonne NULLABLE en base) ; ces lignes sont exclues à l'agrégation vers trafic_horaire (WHERE forfait_id IS NOT NULL), pas ici.

Codes forfait inconnus: si un forfait_id rencontré n'existe pas encore dans `forfait` (catalogue incomplet, ou forfait occasionnel jamais catalogué),
une ligne stub est auto-créée (nom=NULL, connu=FALSE) avant l'insertion, plutôt que de rejeter la ligne sur la FK. Ces codes peuvent eux-mêmes être
les forfaits occasionnels impactant la QoS - on ne les exclut donc jamais du pipeline. Voir occurrence_forfait_jour (vue) pour leur fréquence réelle.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Optional

# NB : psycopg (et src.donnees.db) ne sont chargés qu'au moment de l'insertion, dans _inserer().
# Ainsi le parsing/mapping peut être testé sans base ni libpq installés (même pattern que CollecteurCDR).

CDR_TYPE_ACHAT = "forfait"  # seule valeur connue à ce jour pour un événement d'achat/activation


@dataclass
class LigneBilling:
    """Une ligne prête à insérer dans consommation_forfait."""
    bucket: dt.datetime
    served_isdn: str
    forfait_id: Optional[str]
    cdr_type: str
    duration: Optional[int]
    data_volume: Optional[float]


class CollecteurBilling:
    """Parse un fichier JSON SurePay et insère les événements de consommation dans consommation_forfait."""

    # ------------------------------- lecture / parsing -------------------------------------
    def _iter_records(self, chemin: Path) -> Iterator[dict]:
        """Rend les événements un par un.
        Contrairement aux fichiers CDR réseau ({"templates": [...]}), chaque fichier SurePay vu jusqu'ici contient UN SEUL événement JSON à la racine (pas de liste).
        On gère les 2 formes par prudence : objet unique, ou liste d'objets, ou {"templates": [...]} si jamais SurePay change de format d'export.
        """
        with open(chemin, encoding="utf-8") as f:
            doc = json.load(f)

        if isinstance(doc, list):
            yield from doc
        elif isinstance(doc, dict) and isinstance(doc.get("templates"), list):
            yield from doc["templates"]
        elif isinstance(doc, dict):
            yield doc
        else:
            raise ValueError(f"{chemin.name}: structure JSON inattendue (ni objet, ni liste).")

    def _source(self, rec: dict) -> dict:
        """Les échantillons SurePay portent les champs utiles sous "_source". On retombe sur l'objet racine si "_source" est absent, pour rester tolérant à un format différent."""
        return rec.get("_source", rec)

    def _parse_datetime(self, valeur: Optional[str]) -> Optional[dt.datetime]:
        """dateTime ISO, parfois sans secondes (ex. '2026-07-07T10:58') ni fuseau. Interprété en UTC, comme pour les CDR réseau (même point ouvert: fuseau réel à confirmer)."""
        if not valeur:
            return None
        try:
            naive = dt.datetime.fromisoformat(valeur)
        except ValueError:
            return None
        return naive.replace(tzinfo=dt.timezone.utc)

    def _int(self, valeur) -> Optional[int]:
        if valeur in (None, ""):
            return None
        try:
            return int(valeur)
        except (TypeError, ValueError):
            return None

    def _num(self, valeur) -> Optional[float]:
        if valeur in (None, ""):
            return None
        try:
            return float(valeur)
        except (TypeError, ValueError):
            return None

    def _mapper(self, rec: dict) -> Optional[LigneBilling]:
        """Transforme un événement de CONSOMMATION en LigneBilling. Rend None si inexploitable. Les événements d'achat (cdrType="forfait") sont filtrés en amont, dans parser_fichier()."""
        src = self._source(rec)

        cdr_type_src = src.get("cdrType") or ""
        if not cdr_type_src:
            return None  # cdrType absent : on ne devine pas, on saute (à journaliser)

        served_isdn = src.get("servedIsdn") or None
        if served_isdn is None:
            return None  # served_isdn est NOT NULL en base

        ts = self._parse_datetime(src.get("dateTime"))
        if ts is None:
            return None  # bucket est NOT NULL / clé de partition

        # typeForfait vide -> débit direct du solde principal, pas d'un forfait (forfait_id NULL).
        forfait_id = src.get("typeForfait") or None

        return LigneBilling(
            bucket=ts,
            served_isdn=served_isdn,
            forfait_id=forfait_id,
            cdr_type=cdr_type_src,
            duration=self._int(src.get("duration")),
            data_volume=self._num(src.get("dataVolume")),
        )

    # ------------------------------- insertion -------------------------------------
    _COLONNES = ("bucket", "served_isdn", "forfait_id", "cdr_type", "duration", "data_volume")

    def parser_fichier(self, chemin: str | Path, operateur_id: str) -> dict:
        """Parse le fichier et insère les événements de consommation dans consommation_forfait via COPY. Les événements d'achat
        sont comptés à part (achats), pas dans ignores : ce sont des lignes valides, juste hors périmètre de cette table.
        operateur_id : opérateur du fichier (ex. 'YAS', 'MOOV') - utilisé uniquement pour auto-créer un stub `forfait` (connu=FALSE) si un code rencontré n'existe pas encore.
        Retourne un récapitulatif {lus, inseres, achats, ignores}."""
        chemin = Path(chemin)
        lignes: list[LigneBilling] = []
        lus = achats = ignores = 0
        for rec in self._iter_records(chemin):
            lus += 1
            src = self._source(rec)
            if (src.get("cdrType") or "") == CDR_TYPE_ACHAT:
                achats += 1
                continue
            ligne = self._mapper(rec)
            if ligne is None:
                ignores += 1
                continue
            lignes.append(ligne)

        inseres = self._inserer(lignes, operateur_id)
        return {"lus": lus, "inseres": inseres, "achats": achats, "ignores": ignores,
                "fichier": chemin.name}

    def _inserer(self, lignes: Iterable[LigneBilling], operateur_id: str) -> int:
        from src.donnees.db import connexion  # import tardif (voir en-tête)

        lignes = list(lignes)
        if not lignes:
            return 0

        codes = {l.forfait_id for l in lignes if l.forfait_id}

        cols = ", ".join(self._COLONNES)
        n = 0
        with connexion() as conn:
            with conn.cursor() as cur:
                if codes:
                    cur.executemany(
                        "INSERT INTO forfait (id, nom, operateur_id, connu) VALUES (%s, NULL, %s, FALSE) "
                        "ON CONFLICT (id) DO NOTHING",
                        [(code, operateur_id) for code in codes],
                    )
                with cur.copy(
                    f"COPY consommation_forfait ({cols}) FROM STDIN"
                ) as copy:
                    for l in lignes:
                        copy.write_row((
                            l.bucket, l.served_isdn, l.forfait_id,
                            l.cdr_type, l.duration, l.data_volume,
                        ))
                        n += 1
        return n


def main(argv: Optional[list[str]] = None) -> None:
    p = argparse.ArgumentParser(description="Collecte de consommation billing (SurePay) vers TimescaleDB.")
    p.add_argument("fichier", help="Chemin du fichier JSON SurePay")
    p.add_argument("--operateur", default="YAS",
                    help="Code operateur du fichier (ex. YAS, MOOV) - utilise seulement si un code "
                        "forfait inconnu doit etre auto-cree. Defaut : YAS.")
    args = p.parse_args(argv)

    collecteur = CollecteurBilling()
    resume = collecteur.parser_fichier(args.fichier, args.operateur)
    print(
        f"[{resume['fichier']}] lus={resume['lus']} "
        f"insérés={resume['inseres']} achats_ignorés={resume['achats']} "
        f"ignorés={resume['ignores']}"
    )


if __name__ == "__main__":
    main()