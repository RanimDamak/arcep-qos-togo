"""CollecteurCDR - lit un fichier CDR JSON et insère les lignes dans `enregistrement_cdr` (TimescaleDB).

Structure réelle des fichiers JSON (vérifiée sur les échantillons MOOV/YAS) : { "templates": [ { ...31 champs... }, ... ] }

Chaque élément de `templates` = un CDR. Champs utiles pour nous : cdrType, cdrReference, dateTime, uplink, downlink, typeForfait, tarifPlan, qosNegotiated.

QoS (mis à jour) : les CDR portent l'IE QoS TS 24.008 §10.5.6.5 (PDP context, 15/16 octets - PAS le profil
EPC TS 29.212/29.274 supposé initialement). Champ `qosNegotiated` (hex string) présent uniquement sur les
nouveaux enregistrements côté source - absent sur les anciens échantillons déjà collectés, ce qui est
normal et pas une erreur de parsing. Décodage : voir src/collecte/qos_decoder.py.

Traffic Class (octet 6) différé (décision du chef) : Transfer Delay / Traffic Handling Priority /
Signalling Indication sont décodés SANS filtre de validité pour l'instant (bruit potentiel accepté).

Voix vs data : `qosNegotiated` ne concerne QUE les CDR data (S-CDR/PGW-CDR/SGW-CDR). Pour les CDR voix (MSC-CDR), la qualité est portée par `half_rate`/`hr_ratio`,
pas par un décodage QoS - ces deux familles de colonnes sont mutuellement exclusives selon `type_cdr`, jamais les deux renseignées sur la même ligne.

Le mode mockup n'invente PAS de fausses données de production : il remplit les colonnes QoS avec des valeurs
UNIQUEMENT pour valider le pipeline, et marque qos_spec_version='MOCK' pour qu'on ne les confonde jamais avec du réel.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Optional

from src.collecte.qos_decoder import decode_qos_negotiated

# NB : psycopg (et src.donnees.db, qui l'importe) ne sont chargés qu'au moment de l'insertion,
# dans _inserer(). Ainsi, le parsing/mapping peut être testé sans base ni libpq installés.


# ----------- Mapping cdrType (opérateur) -> type_cdr (contrainte du schéma) -----------
# Le schéma contraint type_cdr IN ('S-CDR','PGW-CDR','SGW-CDR','MSC-CDR').
# Les fichiers opérateur utilisent d'autres codes ; on traduit ici.
#   gw            = data (passerelle)      -> PGW-CDR  (cf. note : 'gw' générique, PGW/SGW non distingués -> PGW par défaut)
#   moc/mtc       = appels voix (orig/term) -> MSC-CDR
#   smo/smt       = SMS (orig/term)         -> MSC-CDR (domaine CS)
MAP_TYPE_CDR = {
    "gw": "PGW-CDR",
    "moc": "MSC-CDR",
    "mtc": "MSC-CDR",
    "smo": "MSC-CDR",
    "smt": "MSC-CDR",
}
# Types considérés « voix / CS » (pour l'indicateur half_rate).
TYPES_VOIX = {"moc", "mtc", "smo", "smt"}

@dataclass
class LigneCDR:
    """Une ligne prête à insérer dans enregistrement_cdr."""
    cdr_timestamp: dt.datetime
    cdr_reference: Optional[str]
    type_cdr: str
    uplink: Optional[float]
    downlink: Optional[float]
    qos_negotiated_raw: Optional[bytes]
    delay_class_score: Optional[int]
    precedence_class_score: Optional[int]
    max_bitrate_ul_kbps: Optional[int]
    max_bitrate_dl_kbps: Optional[int]
    residual_ber_score: Optional[int]
    sdu_error_ratio_score: Optional[int]
    transfer_delay_ms: Optional[int]
    transfer_delay_score: Optional[int]
    traffic_handling_priority_score: Optional[int]
    signalling_indication: Optional[bool]
    qos_spec_version: str
    half_rate: bool
    hr_ratio: float


class CollecteurCDR:
    """Parse un fichier CDR JSON et insère dans enregistrement_cdr."""

    def __init__(self, mock_qos: bool = False):
        self.mock_qos = mock_qos

    # ------------------------------- lecture / parsing -------------------------------------
    def _iter_records(self, chemin: Path) -> Iterator[dict]:
        """Rend les CDR un par un. Note : les échantillons tiennent en mémoire (8 Mo), mais on garde un itérateur pour ne pas dépendre de la taille."""
        with open(chemin, encoding="utf-8") as f:
            doc = json.load(f)
        records = doc.get("templates", [])
        if not isinstance(records, list):
            raise ValueError(
                f"{chemin.name}: clé 'templates' absente ou non-liste "
                f"(structure inattendue)."
            )
        yield from records

    def _parse_datetime(self, valeur: Optional[str]) -> Optional[dt.datetime]:
        """dateTime ISO sans fuseau (ex. '2026-06-24T10:32:27'). On l'interprète en UTC (à confirmer avec l'équipe : fuseau réel des CDR)."""
        if not valeur:
            return None
        try:
            naive = dt.datetime.fromisoformat(valeur)
        except ValueError:
            return None
        return naive.replace(tzinfo=dt.timezone.utc)

    def _num(self, valeur) -> Optional[float]:
        """uplink/downlink arrivent en float ou 0 ; None si absent/invalide."""
        if valeur in (None, ""):
            return None
        try:
            return float(valeur)
        except (TypeError, ValueError):
            return None

    def _parse_qos_negotiated(self, valeur) -> Optional[bytes]:
        """Convertit le champ JSON qosNegotiated (hex string) en bytes. None si absent/invalide - absent est un cas NORMAL
        sur les anciens enregistrements (champ pas encore fourni par la source), pas une erreur à journaliser bruyamment."""
        if not valeur or not isinstance(valeur, str):
            return None
        try:
            return bytes.fromhex(valeur.strip())
        except ValueError:
            return None  # hex malformé - ligne traitée comme QoS absente, pas de crash sur tout le fichier

    def _qos_champs_vides(self) -> dict:
        return {
            "delay_class_score": None, "precedence_class_score": None,
            "max_bitrate_ul_kbps": None, "max_bitrate_dl_kbps": None,
            "residual_ber_score": None, "sdu_error_ratio_score": None,
            "transfer_delay_ms": None, "transfer_delay_score": None,
            "traffic_handling_priority_score": None, "signalling_indication": None,
        }

    def _mock_qos(self) -> dict:
        """Valeurs QoS FICTIVES pour tester la chaîne (mode --mock uniquement, CDR data seulement - jamais appelé côté voix, voir _mapper).
        Traffic Class différé : les 3 champs conditionnels sont générés sans restriction, comme le décodage réel actuel."""
        max_ul = random.choice([None] + list(range(1, 8640)))
        max_dl = random.choice([None] + list(range(1, 8640)))

        return {
            "delay_class_score": random.randint(1, 4),
            "precedence_class_score": random.randint(1, 3),
            "max_bitrate_ul_kbps": max_ul,
            "max_bitrate_dl_kbps": max_dl,
            "residual_ber_score": random.randint(1, 9),
            "sdu_error_ratio_score": random.randint(1, 7),
            "transfer_delay_ms": random.randint(10, 4000),
            "transfer_delay_score": random.randint(1, 3),
            "traffic_handling_priority_score": random.randint(1, 3),
            "signalling_indication": random.choice([True, False]),
        }

    def _mapper(self, rec: dict) -> Optional[LigneCDR]:
        """Transforme un CDR brut en LigneCDR. Rend None si inexploitable."""
        cdr_type_src = (rec.get("cdrType") or "").lower()
        type_cdr = MAP_TYPE_CDR.get(cdr_type_src)
        if type_cdr is None:
            # cdrType inconnu : on ne devine pas, on saute (à journaliser).
            return None

        ts = self._parse_datetime(rec.get("dateTime"))
        if ts is None:
            return None  # cdr_timestamp est NOT NULL / clé de partition

        est_voix = cdr_type_src in TYPES_VOIX
        qos_raw: Optional[bytes] = None

        # QoS (qosNegotiated) ne concerne QUE les CDR data (S-CDR/PGW-CDR/SGW-CDR) - pour la voix (MSC-CDR), la qualité passe par half_rate/hr_ratio,
        # pas par un décodage QoS. On ne tente même pas le décodage côté voix, même si le champ apparaissait par erreur dans le JSON.
        if est_voix:
            q = self._qos_champs_vides()
            spec_version = "voix"
        elif self.mock_qos:
            q = self._mock_qos()
            spec_version = "MOCK"
        else:
            qos_raw = self._parse_qos_negotiated(rec.get("qosNegotiated"))
            if qos_raw is not None:
                try:
                    q = decode_qos_negotiated(qos_raw)
                    spec_version = "24.008"
                except ValueError:
                    q = self._qos_champs_vides()
                    qos_raw = None  # bytes trop courts/invalides - on ne garde pas un raw qu'on ne sait pas décoder
                    spec_version = "absent"
            else:
                q = self._qos_champs_vides()
                spec_version = "absent"  # ancien enregistrement, champ pas encore fourni par la source - cas normal

        # NB : les échantillons actuels ne portent pas l'info half-rate, donc on laisse les valeurs par défaut pour l'instant (half_rate=False,
        # hr_ratio=0.0) même côté voix - à réviser dès que la source fournit ce champ (même situation que qosNegotiated avant son ajout).

        return LigneCDR(
            cdr_timestamp=ts,
            cdr_reference=rec.get("cdrReference"),
            type_cdr=type_cdr,
            uplink=self._num(rec.get("uplink")),
            downlink=self._num(rec.get("downlink")),
            qos_negotiated_raw=qos_raw,
            delay_class_score=q["delay_class_score"],
            precedence_class_score=q["precedence_class_score"],
            max_bitrate_ul_kbps=q["max_bitrate_ul_kbps"],
            max_bitrate_dl_kbps=q["max_bitrate_dl_kbps"],
            residual_ber_score=q["residual_ber_score"],
            sdu_error_ratio_score=q["sdu_error_ratio_score"],
            transfer_delay_ms=q["transfer_delay_ms"],
            transfer_delay_score=q["transfer_delay_score"],
            traffic_handling_priority_score=q["traffic_handling_priority_score"],
            signalling_indication=q["signalling_indication"],
            qos_spec_version=spec_version,
            half_rate=False,           # indicateur réel non présent -> défaut
            hr_ratio=0.0,
        )

    # ------------------------------- insertion -------------------------------------
    _COLONNES = (
        "cdr_timestamp", "cdr_reference", "type_cdr", "uplink", "downlink", "qos_negotiated_raw",
        "delay_class_score", "precedence_class_score",
        "max_bitrate_ul_kbps", "max_bitrate_dl_kbps",
        "residual_ber_score", "sdu_error_ratio_score",
        "transfer_delay_ms", "transfer_delay_score",
        "traffic_handling_priority_score", "signalling_indication",
        "qos_spec_version", "half_rate", "hr_ratio",
    )

    def parser_fichier(self, chemin: str | Path) -> dict:
        """Parse le fichier et insère dans enregistrement_cdr via COPY (rapide).
        Retourne un petit récapitulatif {lus, insérés, ignorés}."""
        chemin = Path(chemin)
        lignes: list[LigneCDR] = []
        lus = ignores = 0
        for rec in self._iter_records(chemin):
            lus += 1
            ligne = self._mapper(rec)
            if ligne is None:
                ignores += 1
                continue

            lignes.append(ligne)

        inseres = self._inserer(lignes)
        return {"lus": lus, "inseres": inseres, "ignores": ignores,
                "fichier": chemin.name, "mock_qos": self.mock_qos}

    def _inserer(self, lignes: Iterable[LigneCDR]) -> int:
        from src.donnees.db import connexion  # import tardif (voir en-tête)

        lignes = list(lignes)
        if not lignes:
            return 0
        cols = ", ".join(self._COLONNES)
        n = 0
        with connexion() as conn:
            with conn.cursor() as cur:
                # COPY : le chemin rapide de psycopg3 pour l'insertion en masse.
                with cur.copy(
                    f"COPY enregistrement_cdr ({cols}) FROM STDIN"
                ) as copy:
                    for l in lignes:
                        copy.write_row((
                            l.cdr_timestamp, l.cdr_reference, l.type_cdr, l.uplink, l.downlink, l.qos_negotiated_raw,
                            l.delay_class_score, l.precedence_class_score,
                            l.max_bitrate_ul_kbps, l.max_bitrate_dl_kbps,
                            l.residual_ber_score, l.sdu_error_ratio_score,
                            l.transfer_delay_ms, l.transfer_delay_score,
                            l.traffic_handling_priority_score, l.signalling_indication,
                            l.qos_spec_version, l.half_rate, l.hr_ratio,
                        ))
                        n += 1
        return n


def main(argv: Optional[list[str]] = None) -> None:
    p = argparse.ArgumentParser(description="Collecte de CDR vers TimescaleDB.")
    p.add_argument("fichier", help="Chemin du fichier CDR JSON")
    p.add_argument("--mock", action="store_true", help="Remplit des QoS FICTIVES (spec_version=MOCK) pour tester la chaîne quand le flux ne porte pas encore la QoS.")
    args = p.parse_args(argv)

    collecteur = CollecteurCDR(mock_qos=args.mock)
    resume = collecteur.parser_fichier(args.fichier)
    print(
        f"[{resume['fichier']}] lus={resume['lus']} "
        f"insérés={resume['inseres']} ignorés={resume['ignores']} "
        f"mock_qos={resume['mock_qos']}"
    )


if __name__ == "__main__":
    main()
