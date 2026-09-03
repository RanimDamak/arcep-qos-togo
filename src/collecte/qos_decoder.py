"""decode_qos_negotiated - décodage du champ qosNegotiated (IE QoS TS 24.008 §10.5.6.5).

Ne couvre QUE les 9 champs validés (voir Analyse_QoS_15octets.md, version finale). Traffic Class (octet 6) est DIFFÉRÉ - décision explicite : Transfer Delay, Traffic Handling
Priority et Signalling Indication sont décodés SANS filtre de validité pour le moment (la spec les dit "ignorés" selon la classe de trafic, mais Traffic Class n'étant pas encore
capturé, on accepte le bruit potentiel plutôt que de bloquer). Pas les octets 15/16 (extension bitrate), pas les champs hors périmètre (reliability class, peak/mean throughput, etc).

IMPORTANT - alignement des octets :
Sur les 5 échantillons réels vérifiés, `qosNegotiated` fait systématiquement 15 octets, avec un premier octet qui n'appartient PAS à l'IE TS 24.008 (confirmé empiriquement : sans
ce décalage, Traffic Class décode systématiquement en "000/reserved" sur tous les échantillons ; avec le décalage, on obtient des valeurs valides et cohérentes avec rATType).
L'octet 3 réel de la spec commence donc au 2e octet de la chaîne. Origine de cet octet 0 non confirmée (probablement un octet spécifique à l'encodage CDR du vendor) - conservé
dans le résultat sous 'octet0_inconnu' plutôt que jeté, au cas où il s'avère utile plus tard.
"""
from __future__ import annotations

from typing import Optional, TypedDict


class QoSDecode(TypedDict):
    octet0_inconnu: Optional[int]
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


# rang = severite reelle (1=meilleur -> 9=pire) - PAS la valeur binaire brute (non monotone), cf. Analyse_QoS_15octets.md
_RESIDUAL_BER_SCORE = {
    0b1001: 1, 0b1000: 2, 0b0111: 3, 0b0110: 4, 0b0101: 5,
    0b0100: 6, 0b0011: 7, 0b0010: 8, 0b0001: 9,
}

# rang = severite reelle (1=meilleur -> 7=pire) - table NON monotone (0111=pire malgre valeur binaire faible)
_SDU_ERROR_SCORE = {
    0b0110: 1, 0b0101: 2, 0b0100: 3, 0b0011: 4, 0b0010: 5, 0b0001: 6, 0b0111: 7,
}


def _bits(byte_val: int, hi: int, lo: int) -> int:
    """Extrait les bits [hi..lo] (numerotation 3GPP : bit8=MSB, bit1=LSB), hi/lo inclus."""
    n = hi - lo + 1
    return (byte_val >> (lo - 1)) & ((1 << n) - 1)


def _decode_max_bitrate(v: int) -> Optional[int]:
    """Octet 8 ou 9 complet -> debit en kbps. None si subscribed/reserved (valeur 0)."""
    if v == 0:
        return None
    if 1 <= v <= 63:
        return v
    if 64 <= v <= 127:
        return 64 + (v - 64) * 8
    if 128 <= v <= 254:
        return 576 + (v - 128) * 64
    if v == 255:
        return 0  # debit nul explicite (distinct de "non negocie")
    return None


def _bitrate_score(kbps: Optional[int]) -> Optional[int]:
    """1=meilleur (debit eleve) -> 3=pire (debit faible). Debit faible = pire, PAS l'inverse."""
    if kbps is None:
        return None
    if kbps >= 576:
        return 1
    if kbps >= 64:
        return 2
    return 3


def _decode_transfer_delay(v: int) -> Optional[int]:
    """Octet 11 bits 8-3 -> delai en ms. None si subscribed/reserved (0) ou reserved (63)."""
    if v == 0 or v == 63:
        return None
    if 1 <= v <= 15:
        return v * 10
    if 16 <= v <= 31:
        return 200 + (v - 16) * 50
    if 32 <= v <= 62:
        return 1000 + (v - 32) * 100
    return None


def _transfer_delay_score(ms: Optional[int]) -> Optional[int]:
    if ms is None:
        return None
    if ms <= 150:
        return 1
    if ms <= 950:
        return 2
    return 3


def decode_qos_negotiated(raw: bytes) -> QoSDecode:
    """Decode le contenu de qos_negotiated_raw (bytes) selon TS 24.008 par.10.5.6.5.

    Ne leve pas d'exception sur un octet de moins que prevu pour un champ individuel - retourne None pour ce champ precis plutot que de faire echouer tout le decodage
    (coherent avec le traitement des CDR malformes ailleurs dans le pipeline : on ignore la ligne fautive, on ne bloque pas tout le fichier).

    Leve ValueError si `raw` est trop court pour couvrir ne serait-ce que l'octet 11
    (le champ utile le plus loin dans l'IE parmi les 9 demandes) - en dessous de 10 octets, le decodage n'a de toute facon aucun sens.
    """
    if raw is None or len(raw) < 10:
        raise ValueError(
            f"qos_negotiated_raw trop court pour decoder ({0 if raw is None else len(raw)} octets, 10 minimum attendus)"
        )

    # decalage empirique : byte[0] = octet inconnu hors-spec, byte[1] = octet3 reel (voir docstring module)
    octet0 = raw[0]

    def octet(n: int) -> Optional[int]:
        idx = n - 2  # octet3 -> index1, octet4 -> index2, etc.
        return raw[idx] if idx < len(raw) else None

    o3, o4 = octet(3), octet(4)
    o8, o9, o10, o11 = octet(8), octet(9), octet(10), octet(11)
    o14 = octet(14)

    delay_class = _bits(o3, 6, 4) if o3 is not None else None
    precedence = _bits(o4, 3, 1) if o4 is not None else None

    max_ul_kbps = _decode_max_bitrate(o8) if o8 is not None else None
    max_dl_kbps = _decode_max_bitrate(o9) if o9 is not None else None

    residual_ber_raw = _bits(o10, 8, 5) if o10 is not None else None
    sdu_err_raw = _bits(o10, 4, 1) if o10 is not None else None

    transfer_delay_raw = _bits(o11, 8, 3) if o11 is not None else None
    thp_raw = _bits(o11, 2, 1) if o11 is not None else None

    sig_ind_raw = _bits(o14, 5, 5) if o14 is not None else None

    td_ms = _decode_transfer_delay(transfer_delay_raw) if transfer_delay_raw is not None else None

    # Traffic Class differe (decision du chef) - pas de filtre de validite pour le moment,
    # les 3 champs conditionnels (transfer_delay/thp/signalling) sont decodes sans condition.
    return QoSDecode(
        octet0_inconnu=octet0,
        delay_class_score=(delay_class if delay_class else None),
        precedence_class_score=(precedence if precedence else None),
        max_bitrate_ul_kbps=max_ul_kbps,
        max_bitrate_dl_kbps=max_dl_kbps,
        residual_ber_score=(_RESIDUAL_BER_SCORE.get(residual_ber_raw) if residual_ber_raw else None),
        sdu_error_ratio_score=(_SDU_ERROR_SCORE.get(sdu_err_raw) if sdu_err_raw else None),
        transfer_delay_ms=td_ms,
        transfer_delay_score=_transfer_delay_score(td_ms),
        traffic_handling_priority_score=(thp_raw if thp_raw else None),
        signalling_indication=bool(sig_ind_raw),
    )
