"""CollecteurQualcop - genere des donnees de congestion FICTIVES et les insere dans `congestion_horaire`(TimescaleDB). Aucun echantillon
QUALCOP reel n'a ete recu a ce jour : ce collecteur n'a donc PAS de mode "reel" pour l'instant, contrairement a CollecteurCDR/CollecteurBilling
qui parsent deja des fichiers reels. Il ne fait que generer des valeurs mock pour permettre de tester la chaine d'agregation
-> ObservationHoraire.congestion -> detection 3-sigma de bout en bout, en attendant le vrai flux. Un parser reel remplacera generer_mock()
une fois le format QUALCOP effectivement recu - la methode inserer() (COPY vers congestion_horaire) restera, elle, inchangee.

Format retenu pour la congestion (decision validee) :
  * RESEAU ENTIER, PAS de decoupage par cellule: le flux QUALCOP fournit un JSON par heure avec exactement 3 valeurs (une par domaine) - pas de cellule_id.
  * Un taux en FRACTION (0-1, PAS un pourcentage 0-100) : generalement < 0.1, mais la borne haute autorisee reste 1 par prudence (pas de risque).
  * 3 types independants par heure : CS (voix, MSC), PS (data 3G), EPS (data 4G) - jamais melanges sur une meme ligne.

Les lignes generees ont est_mock=TRUE (colonne dediee dans congestion_horaire), pour rester trivialement identifiables (DELETE FROM congestion_horaire
WHERE est_mock) une fois le vrai flux QUALCOP branche, meme logique que qos_spec_version='MOCK' dans CollecteurCDR. Remplace l'ancien marquage par
prefixe sur cellule_id, qui n'existe plus depuis la suppression de cette colonne (le flux etant reseau entier, il n'y avait plus de cellule a identifier).
"""
from __future__ import annotations

import argparse
import datetime as dt
import random
from dataclasses import dataclass
from typing import Iterable, Optional

TYPES_CONGESTION = ("CS", "PS", "EPS")

# Plage de valeurs realiste (decision validee) : fraction 0-1, 0-0.05 typique, jusqu'a ~0.1 en pic rare.
# La contrainte en base autorise jusqu'a 1.0 (prudence), mais le mock reste realiste pour des tests utiles.
VALEUR_TYPIQUE = (0.0, 0.05)
VALEUR_PIC = (0.05, 0.1)
PROBA_PIC = 0.03  # ~3% des lignes simulent un pic (utile pour exercer la detection a 3 sigma)

@dataclass
class LigneCongestion:
    """Une ligne prete a inserer dans congestion_horaire."""
    bucket: dt.datetime
    type_congestion: str
    valeur: float
    est_mock: bool = True


class CollecteurQualcop:
    """Genere des lignes de congestion FICTIVES et les insere dans congestion_horaire. Pas de parser_fichier() : aucun format QUALCOP reel connu a ce jour."""

    def __init__(self, prefixe_mock: str = "MOCK-"):
        self.prefixe_mock = prefixe_mock

    def _valeur_mock(self) -> float:
        if random.random() < PROBA_PIC:
            return round(random.uniform(*VALEUR_PIC), 4)
        return round(random.uniform(*VALEUR_TYPIQUE), 4)

    def generer_mock(self, debut: dt.datetime, fin: dt.datetime) -> list[LigneCongestion]:
        """Genere une ligne par (heure, type) sur [debut, fin[, au pas horaire - reseau entier, pas de boucle sur des cellules (le flux QUALCOP ne fournit plus ce decoupage)."""
        lignes: list[LigneCongestion] = []
        bucket = debut.replace(minute=0, second=0, microsecond=0)
        while bucket < fin:
            for type_congestion in TYPES_CONGESTION:
                lignes.append(LigneCongestion(
                    bucket=bucket,
                    type_congestion=type_congestion,
                    valeur=self._valeur_mock(),
                ))
            bucket += dt.timedelta(hours=1)
        return lignes

    # ------------------------------- insertion -------------------------------------
    _COLONNES = ("bucket", "type_congestion", "valeur", "est_mock")

    def inserer(self, lignes: Iterable[LigneCongestion]) -> int:
        from src.donnees.db import connexion  # import tardif (meme pattern que les autres collecteurs)

        lignes = list(lignes)
        if not lignes:
            return 0
        cols = ", ".join(self._COLONNES)
        n = 0
        with connexion() as conn:
            with conn.cursor() as cur:
                # COPY : meme chemin rapide que CollecteurCDR / CollecteurBilling.
                with cur.copy(
                    f"COPY congestion_horaire ({cols}) FROM STDIN"
                ) as copy:
                    for l in lignes:
                        copy.write_row((l.bucket, l.type_congestion, l.valeur, l.est_mock))
                        n += 1
        return n

    def generer_et_inserer(self, debut: dt.datetime, fin: dt.datetime) -> dict:
        """Genere puis insere en une seule passe. Retourne un recapitulatif."""
        lignes = self.generer_mock(debut, fin)
        inseres = self.inserer(lignes)
        return {"generees": len(lignes), "inseres": inseres, "periode": f"{debut.isoformat()} -> {fin.isoformat()}"}

def _parse_date(valeur: str) -> dt.datetime:
    """Date/heure ISO ; interpretee en UTC (meme convention que CollecteurCDR/CollecteurBilling)."""
    naive_ou_aware = dt.datetime.fromisoformat(valeur)
    if naive_ou_aware.tzinfo is None:
        return naive_ou_aware.replace(tzinfo=dt.timezone.utc)
    return naive_ou_aware


def main(argv: Optional[list[str]] = None) -> None:
    p = argparse.ArgumentParser(
        description="Genere des donnees de congestion FICTIVES, reseau entier (CS/PS/EPS), et les insere dans congestion_horaire. Aucun flux QUALCOP reel disponible a ce jour."
    )
    p.add_argument("--debut", required=True, help="Date/heure de debut ISO (ex. 2026-07-01T00:00:00)")
    p.add_argument("--fin", required=True, help="Date/heure de fin ISO, exclusive (ex. 2026-07-02T00:00:00)")
    args = p.parse_args(argv)

    collecteur = CollecteurQualcop()
    resume = collecteur.generer_et_inserer(
        _parse_date(args.debut), _parse_date(args.fin)
    )
    print(
        f"[MOCK] periode={resume['periode']}"
        f"generees={resume['generees']} inserees={resume['inseres']}"
    )


if __name__ == "__main__":
    main()