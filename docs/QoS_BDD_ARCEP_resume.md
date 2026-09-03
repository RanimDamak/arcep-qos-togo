# Résumé - Vérification QoS Negotiated & Architecture BDD ARCEP

> **Mise à jour** : les sections 1-6 et 8 (vérification de spec ASN.1/TS 32.298, classification PGW/MSC, décodage hexadécimal, raisonnement BYTEA) sont de la documentation technique stable - elles ne dépendent d'aucune décision projet et restent valides telles quelles. Les sections 7, 9, 10.5, 11 et 12 en revanche décrivaient l'état du schéma et les questions ouvertes **à ce moment du projet** ; le schéma a évolué plusieurs fois depuis (voir `README.md`, qui fait foi pour l'état actuel). Ces sections ont été annotées ci-dessous plutôt que réécrites, pour garder la trace de comment on est arrivé aux décisions actuelles.

> **Mise à jour (format QoS réel)** : la section 5 complète `EPCQoSInformation` en partant du principe que c'est ce format que portent nos CDR - hypothèse de travail de l'époque, sur demande de Mr. Ramzi. On sait depuis que ce n'est pas le cas : les CDR réels portent l'IE QoS TS 24.008 (`qosNegotiated`, 15/16 octets - Delay class, Precedence class, débits, BER, etc.), pas le profil EPC (TS 29.212, 22+ octets). La section 5 reste correcte en tant que documentation de la structure EPC elle-même (vérifiée contre la spec), mais ne décrit pas ce que nos CDR portent réellement. Détail du format réel et de son décodage : voir `Analyse_QoS_15octets.md` et `README.md` §6.

## Contexte

Mr. Ramzi a transmis un extrait ASN.1 décrivant le champ `QoS Negotiated` des CDR, en demandant de vérifier sa validité face à la version la plus récente de la spec (confusion initiale sur "TS GSM 04.08", qui n'est qu'une référence interne citée dans le corps d'un autre document, pas un fichier à télécharger séparément).

Document source vérifié : **`ts_132298v170400p.pdf`** - confirmé via métadonnées PDF (`pdfinfo`) comme étant **3GPP TS 32.298 version 17.4.0, Release 17**, publié par l'ETSI Secretariat le 13 octobre 2022, 264 pages.

Lien officiel : https://www.etsi.org/deliver/etsi_ts/132200_132299/132298/17.04.00_60/ts_132298v170400p.pdf

Mr. Ramzi a ensuite ajouté la structure `EPCQoSInformation ::= SEQUENCE` et demandé trois compléments : (1) classer chaque élément QoS par domaine PGW (data) vs MSC (voix), (2) compléter `EPCQoSInformation` champ par champ via **TS 29.212**, (3) fournir un exemple de décodage hexadécimal → binaire du `qosNegotiated`. Ce résumé intègre ces trois compléments, les ajustements de l'architecture BDD et du diagramme de classes qui en découlent, et la liste de questions à poser avant de figer le schéma.

---

## 1. Ce qui était obsolète dans le texte envoyé par Mr. Ramzi

Le bloc initial décrivait `QoSInformation` comme une `SEQUENCE` à 5 champs nommés :

```
QoSInformation ::= SEQUENCE
{
reliability     [0] QoSReliability,
delay           [1] QoSDelay,
precedence      [2] QoSPrecedence,
peakThroughput  [3] QoSPeakThroughput,
meanThroughput  [4] QoSMeanThroughput
}
```

Ce modèle correspond à une **version très ancienne** (pré-Release 5, ~2002-2004), où le QoS GPRS de base était encodé via 5 énumérations sur 1 octet chacune, référencées dans **TS GSM 04.08**. Ce modèle est **obsolète** dans toutes les versions actuellement maintenues.

---

## 2. La structure actuelle confirmée (page 169 du PDF, vérifiée par extraction directe)

```
QoSInformation ::= OCTET STRING (SIZE (4..255))
--
-- This octet string
-- is a 1:1 copy of the contents (i.e. starting with octet 5) of the "Bearer Quality of
-- Service" information element specified in TS 29.274 [223].
--
```

**Point clé** : `QoSInformation` n'est plus une séquence ASN.1 lisible avec des champs nommés - c'est un **octet-string opaque** de 4 à 255 octets. Le décodage du contenu binaire est délégué à **TS 29.274** (spec GTPv2-C, "Evolved GPRS Tunnelling Protocol for Control plane"), pas à TS 24.008 comme supposé initialement.

```
DataVolumeGPRS ::= INTEGER
-- -- The volume of data transferred in octets. --
```

---

## 3. Structure complète et finale de `ChangeOfCharCondition`

Vérifiée mot pour mot dans le PDF, pages 164-165 :

```
ChangeOfCharCondition ::= SEQUENCE
--
-- qosRequested and qosNegotiated are used in S-CDR only
-- ePCQoSInformation used in SGW-CDR, PGW-CDR, IPE-CDR, TWAG-CDR and ePDG-CDR only
-- userLocationInformation is used only in S-CDR, SGW-CDR and PGW-CDR
-- chargingID used in PGW-CDR only when Charging per IP-CAN session is active
-- accessAvailabilityChangeReason and relatedChangeOfCharCondition applicable only in PGW-CDR
-- cPCIoTOptimisationIndicator is used in SGW-CDR only
-- aPNRateControl is valid for PGW-CDR only
--
{
qosRequested                            [1]  QoSInformation OPTIONAL,
qosNegotiated                           [2]  QoSInformation OPTIONAL,
dataVolumeGPRSUplink                    [3]  DataVolumeGPRS OPTIONAL,
dataVolumeGPRSDownlink                  [4]  DataVolumeGPRS OPTIONAL,
changeCondition                         [5]  ChangeCondition,
changeTime                              [6]  TimeStamp,
userLocationInformation                 [8]  OCTET STRING OPTIONAL,
ePCQoSInformation                       [9]  EPCQoSInformation OPTIONAL,
chargingID                              [10] ChargingID OPTIONAL,
presenceReportingAreaStatus             [11] PresenceReportingAreaStatus OPTIONAL,
userCSGInformation                      [12] UserCSGInformation OPTIONAL,
diagnostics                             [13] Diagnostics OPTIONAL,
enhancedDiagnostics                     [14] EnhancedDiagnostics OPTIONAL,
rATType                                 [15] RATType OPTIONAL,
accessAvailabilityChangeReason          [16] AccessAvailabilityChangeReason OPTIONAL,
uWANUserLocationInformation             [17] UWANUserLocationInfo OPTIONAL,
relatedChangeOfCharCondition            [18] RelatedChangeOfCharCondition OPTIONAL,
cPCIoTEPSOptimisationIndicator          [19] CPCIoTEPSOptimisationIndicator OPTIONAL,
servingPLMNRateControl                  [20] ServingPLMNRateControl OPTIONAL,
threeGPPPSDataOffStatus                 [21] ThreeGPPPSDataOffStatus OPTIONAL,
listOfPresenceReportingAreaInformation  [22] SEQUENCE OF PresenceReportingAreaInfo OPTIONAL,
aPNRateControl                          [23] APNRateControl OPTIONAL
}
```

**Note sur le tag `[7]` manquant** : c'est normal en ASN.1, les tags ne sont pas obligatoirement consécutifs (le tag `[7]` est utilisé dans `ChangeOfMBMSCondition`, une séquence différente, pas ici).

**Pourquoi le commentaire d'en-tête est essentiel** : il indique quel champ s'applique à quel type de nœud télécom (S-CDR, SGW-CDR, PGW-CDR, etc.). Un même `ChangeOfCharCondition` ne contient jamais les 23 champs simultanément - la présence de chaque champ dépend du nœud qui a généré le CDR. Pour décoder `qosNegotiated` correctement, il faut d'abord identifier quel type de nœud a produit le CDR.

**Éléments inchangés du texte original de Mr. Ramzi** (toujours valides) :
- `TrafficChannel ::= ENUMERATED { fullRate (0), halfRate (1) }`
- `RadioChanRequested ::= ENUMERATED { ... }` (réf. TS 24.008)
- `SpeechVersionIdentifier ::= OCTET STRING (SIZE(1))` (réf. GSM 08.08)

Ces trois éléments concernent le domaine voix (CS), pas le PDP context QoS, et n'ont pas connu de refonte structurelle équivalente.

---

## 4. Classification par domaine : PGW (data) vs MSC (voix)

Les éléments QoS se répartissent en deux domaines réseau distincts, produits par deux familles de nœuds :

| Élément ASN.1 | Domaine | Nœud générateur | CDR |
|---|---|---|---|
| `qosRequested` / `qosNegotiated` (octet-string) | PS (data) | **SGSN** | S-CDR uniquement |
| `ePCQoSInformation` (SEQUENCE) | PS (data) | **SGW / PGW** (+ IPE, TWAG, ePDG) | SGW-/PGW-CDR, … |
| `dataVolumeGPRSUplink` / `Downlink` | PS (data) | SGSN / SGW / PGW | S-/SGW-/PGW-CDR |
| `TrafficChannel` (fullRate/halfRate) | CS (voix) | **MSC** | CDR voix (MOC/MTC) |
| `RadioChanRequested` | CS (voix) | MSC | CDR voix |
| `SpeechVersionIdentifier` | CS (voix) | MSC | CDR voix |

**Point d'attention** : côté data, deux éléments coexistent et ne viennent pas du même nœud - `qosNegotiated` (octet-string) est **S-CDR uniquement (SGSN)**, tandis que `ePCQoSInformation` est l'élément **EPC produit par le PGW/SGW**. Même sémantique QoS (QCI, ARP, MBR/GBR), mais sous deux formes : brute bit-packée pour l'octet-string, déjà structurée en champs ASN.1 pour `ePCQoSInformation`. Le type de décodage dépend donc du nœud d'origine du CDR - ce qui motive l'ajout de `type_cdr` dans la BDD (section 7).

---

## 5. EPCQoSInformation - structure complétée via TS 29.212

```
EPCQoSInformation ::= SEQUENCE
-- See TS 29.212 [220] for more information --
{
  qCI                       [1] INTEGER,
  maxRequestedBandwithUL    [2] INTEGER OPTIONAL,
  maxRequestedBandwithDL    [3] INTEGER OPTIONAL,
  guaranteedBitrateUL       [4] INTEGER OPTIONAL,
  guaranteedBitrateDL       [5] INTEGER OPTIONAL,
  aRP                       [6] INTEGER OPTIONAL,
  aPNAggregateMaxBitrateUL  [7] INTEGER OPTIONAL,
  aPNAggregateMaxBitrateDL  [8] INTEGER OPTIONAL
}
```

Chaque champ correspond à une AVP Diameter définie dans **TS 29.212** (interface Gx), vérifiée directement dans la spec :

| Champ (tag) | AVP TS 29.212 | Type / unité | Sens |
|---|---|---|---|
| `qCI [1]` | QoS-Class-Identifier (1028) | Enumerated | Classe de service standardisée (TS 23.203), hors débits et ARP |
| `maxRequestedBandwithUL [2]` | Max-Requested-Bandwidth-UL (réutilisée, TS 29.214) | bit/s | Débit max autorisé, montant |
| `maxRequestedBandwithDL [3]` | Max-Requested-Bandwidth-DL (réutilisée, TS 29.214) | bit/s | Débit max autorisé, descendant |
| `guaranteedBitrateUL [4]` | Guaranteed-Bitrate-UL (1026) | Unsigned32, bit/s | Débit garanti montant |
| `guaranteedBitrateDL [5]` | Guaranteed-Bitrate-DL (1025) | Unsigned32, bit/s | Débit garanti descendant |
| `aRP [6]` | Allocation-Retention-Priority (1034) | **Grouped** | Priorité d'allocation/rétention - voir détail ci-dessous |
| `aPNAggregateMaxBitrateUL [7]` | APN-Aggregate-Max-Bitrate-UL (1041) | Unsigned32, bit/s | Débit agrégé max montant des bearers non-GBR de l'APN |
| `aPNAggregateMaxBitrateDL [8]` | APN-Aggregate-Max-Bitrate-DL (1040) | Unsigned32, bit/s | Débit agrégé max descendant (non-GBR) |

**qCI (AVP 1028) - valeurs** : 1 à 9 = QCI standardisés (TS 23.203, table 6.1.7) ; 0 et 10-127 = réservés ; 128-254 = spécifiques opérateur ; 255 = réservé.

**aRP (AVP 1034) - pas un simple entier dans TS 29.212**, mais une AVP groupée à trois sous-champs :
- Priority-Level (AVP 1046) : 1 à 15, 1 = priorité la plus haute ;
- Pre-emption-Capability / PCI (AVP 1047) : peut / ne peut pas préempter ;
- Pre-emption-Vulnerability / PVI (AVP 1048) : peut / ne peut pas être préempté.

Ce sont les **trois mêmes sous-champs que l'octet ARP de l'octet-string** (octet 5, section 6). Le champ `aRP [6] INTEGER` porte cette priorité ; PCI/PVI relèvent de la même définition TS 29.212.

**⚠ Unité des débits** : TS 29.212 exprime MBR, GBR et APN-AMBR en **bit/s**. L'octet-string `qosNegotiated` (TS 29.274), lui, les exprime en **kbps**. Deux encodages différents selon la source du CDR - à confirmer sur les CDR réels (question ouverte, section 10).

**En résumé** : `EPCQoSInformation` est la forme déjà décodée de la QoS négociée (CDR PGW/SGW) ; `QoSInformation` (octet-string, S-CDR) doit, lui, être décodé manuellement - voir section 6.

---

## 6. Décodage du QoS Negotiated : hexadécimal → binaire

Carte des octets du Bearer QoS IE (TS 29.274 §8.15, Figure 8.15-1), à partir de l'octet 5 (les 4 octets d'en-tête Type/Length/Spare-Instance sont retirés de l'octet-string) :

| Octet(s) | Champ | Taille | Encodage |
|---|---|---|---|
| 5 | ARP (PVI / PL / PCI) | 1 octet | Bit-packé - voir détail ci-dessous |
| 6 | Label (QCI) | 1 octet | Entier, classe de service |
| 7-11 | MBR Uplink | 5 octets | Entier big-endian, kbps |
| 12-16 | MBR Downlink | 5 octets | Entier big-endian, kbps |
| 17-21 | GBR Uplink | 5 octets | Entier big-endian, kbps |
| 22-26 | GBR Downlink | 5 octets | Entier big-endian, kbps |
| 27… | Extensions | var. | Présent seulement si explicitement spécifié |

**Décodage de l'octet ARP (octet 5)** - convention 3GPP, bit 1 = LSB :

| Bit(s) | Sous-champ | Valeurs |
|---|---|---|
| 8 | Spare | - |
| 7 | PCI (Pre-emption Capability) | 0 = ENABLED · 1 = DISABLED (défaut) |
| 6-3 | PL (Priority Level) | 1 à 15 (1 = plus haute) |
| 2 | Spare | - |
| 1 | PVI (Pre-emption Vulnerability) | 0 = ENABLED (défaut) · 1 = DISABLED |

Extraction binaire : `PVI = octet & 0x01` · `PL = (octet >> 2) & 0x0F` · `PCI = (octet >> 6) & 0x01`.

**Exemple complet décodé et vérifié par exécution** (22 octets) :

```
Hex : 48 01 00 00 00 00 80  00 00 00 01 00  00 00 00 00 80  00 00 00 01 00
```

| Champ | Hex | Binaire | Valeur décodée |
|---|---|---|---|
| ARP (octet 5) | 0x48 | 0100 1000 | PCI=1, PL=2, PVI=0 |
| QCI (octet 6) | 0x01 | 0000 0001 | QCI = 1 (voix, GBR) |
| MBR UL (7-11) | …80 | …1000 0000 | 128 kbps |
| MBR DL (12-16) | …01 00 | …0001 0000 0000 | 256 kbps |
| GBR UL (17-21) | …80 | …1000 0000 | 128 kbps |
| GBR DL (22-26) | …01 00 | …0001 0000 0000 | 256 kbps |

ARP `0x48` = `0100 1000` : bit 7 (PCI) = 1, bits 6-3 (PL) = 0010 = 2, bit 1 (PVI) = 0 - bearer voix prioritaire, non préemptable par défaut.

Décodeur Python de référence (exécuté et vérifié) : `decode_qos_information(octets)` - `octets[0]` = octet 5 (ARP), `octets[1]` = octet 6 (QCI), puis 4×5 octets MBR/GBR en big-endian.

**⚠ Cet encodage est spécifique à TS 32.298 / TS 29.274.** TS 24.301 et TS 36.413 utilisent un encodage différent pour les mêmes paramètres - ne pas réutiliser un décodeur basé sur ces specs pour `qosNegotiated`.

---

## 7. Impact sur l'architecture BDD

> **Mise à jour importante** : la "version révisée" ci-dessous était déjà une étape intermédiaire, et a depuis été remplacée par une version plus aboutie, elle-même documentée (et corrigée) dans `enregistrement_cdr_revise.md`. Pour la structure réellement en base aujourd'hui, se référer à `schema.sql` et `enregistrement_cdr_revise.md` - pas aux tableaux ci-dessous, gardés uniquement pour le fil du raisonnement historique (pourquoi `type_cdr` a été ajouté, pourquoi l'ARP a été redéfini).
> En résumé des changements survenus depuis cette version : `forfait_id` a été retiré de cette table (le lien CDR→forfait ne se fait jamais ici, voir `enregistrement_cdr_revise.md`) ; `jour`/`heure` ont fusionné en une seule colonne `cdr_timestamp` (TIMESTAMPTZ) qui sert aussi de clé de partition ; `qos_pci`/`qos_pvi` ont été ajoutés comme colonnes séparées ; la clé primaire est `(cdr_timestamp, id)`.

### Table `enregistrement_cdr` - AVANT (version initiale, confirmée par Mr. Ramzi)

| Colonne | Type | Contrainte | Description |
|---|---|---|---|
| `qos_valeur` | FLOAT | | Valeur QoS Negotiated décodée (TS 101.393) ❌ |

**Deux erreurs identifiées :**
1. Référence `TS 101.393` incorrecte → devrait être `TS 32.298` / `TS 29.274`
2. Un seul `FLOAT` ne peut pas représenter un octet-string opaque de 4-255 octets

### Table `enregistrement_cdr` - version intermédiaire (intègre la classification PGW/MSC et le décodage - dépassée depuis, voir note ci-dessus)

| Colonne | Type | Contrainte | Description |
|---|---|---|---|
| `jour` | DATE | NOT NULL, partition key | Date de l'enregistrement CDR |
| `heure` | SMALLINT | NOT NULL, CHECK 0-23 | Heure de l'enregistrement |
| `forfait_id` | VARCHAR(30) | FOREIGN KEY → forfait.id | Forfait concerné |
| `type_cdr` | VARCHAR(10) | NOT NULL, CHECK IN ('S-CDR','PGW-CDR','SGW-CDR') | **Nœud source - détermine le chemin de décodage QoS** |
| `qos_negotiated_raw` | BYTEA | | Octet-string brut, **S-CDR uniquement** (TS 29.274). NULL pour PGW/SGW-CDR |
| `qos_qci` | SMALLINT | | QCI (TS 23.203) - décodé de l'octet 6 (S-CDR) ou lu depuis `EPCQoSInformation.qCI` (PGW/SGW-CDR) |
| `qos_arp` | SMALLINT | CHECK 1-15 | **Priority Level décodé (1 = plus haute)**, uniforme pour les deux sources. PCI/PVI non stockés *(corrigé depuis : PCI/PVI sont maintenant stockés en colonnes séparées)* |
| `qos_spec_version` | VARCHAR(10) | DEFAULT 'partiel' | Traçabilité : seuls QCI + ARP(PL) décodés ; MBR/GBR non stockés en colonnes |
| `half_rate` | BOOLEAN | DEFAULT FALSE | Indicateur half-rate actif - côté **CS / MSC** (voix) |
| `hr_ratio` | FLOAT | DEFAULT 0 | Ratio half-rate observé |
| - | | PRIMARY KEY (jour, heure, forfait_id) *(remplacée depuis par (cdr_timestamp, id) - voir note)* | |

**Changements vs la version initiale** (ceux-ci restent valides comme raisonnement, même si la structure a encore évolué depuis) :
1. **Ajout de `type_cdr`** - sans lui, impossible de savoir s'il faut décoder l'octet-string (S-CDR) ou lire les champs déjà structurés (PGW/SGW-CDR). *(Depuis : `'MSC-CDR'` a été ajouté à la contrainte, confirmé nécessaire par les échantillons voix reçus.)*
2. **Colonnes QoS rendues conscientes de la source** - `qos_negotiated_raw` n'est rempli que pour S-CDR ; pour PGW/SGW-CDR la QoS arrive pré-décodée (référence TS 29.212, pas TS 29.274).
3. **`qos_arp` redéfini** en Priority Level décodé (1-15), et non « octet brut » (qui n'existe pas côté EPC, où l'ARP est une structure groupée PL + PCI + PVI).

Non retenu (toujours valide) : pas de colonnes séparées pour MBR/GBR/APN-AMBR - l'algorithme ne les utilise pas ; elles restent accessibles dans `qos_negotiated_raw` pour S-CDR si besoin.

**"Toutes les autres tables restent inchangées" (affirmation de cette version, maintenant FAUSSE)** :
la liste d'origine (`operateur`, `forfait`, `trafic_horaire`, `congestion_horaire`, `observation_horaire`,
`profil_trafic_snapshot`, `profil_qos_snapshot`, `anomalie`, `alerte_qos`, `correlation`, `rapport`)
n'est plus exacte - `congestion_horaire` a été entièrement redessinée (réseau entier, CS/PS/EPS,
échelle 0-1), `observation_horaire` a perdu `trafic_total`, `trafic_horaire.volume_data` est passé en
DL uniquement, et `anomalie`/`alerte_qos`/`correlation` ont été fusionnées en une seule table
`evenement_detecte`. Voir `README.md` pour l'état actuel de chaque table.

---

## 8. Décision BYTEA vs décodage seul - raisonnement et choix final

### Qu'est-ce que BYTEA ?

`BYTEA` ("BYTE Array") est un type PostgreSQL/TimescaleDB qui stocke des **données binaires brutes** - une suite d'octets telle quelle, sans interprétation, contrairement à `FLOAT` (nombre) ou `VARCHAR` (texte). C'est le type fait pour stocker un `OCTET STRING` ASN.1 directement, sans essayer de le comprendre à ce stade.

```python
# Stockage du brut, tel que reçu du CDR
qos_octets = b'\x80\x1f\x05\x96\x00'
cur.execute(
    "INSERT INTO enregistrement_cdr (jour, heure, forfait_id, type_cdr, qos_negotiated_raw) VALUES (%s, %s, %s, %s, %s)",
    (jour, heure, forfait_id, 'S-CDR', qos_octets)
)

# Re-décodage possible plus tard, sans réimporter le CDR source
raw = cur.fetchone()[0]
valeur_decodee = decoder_qos(raw)
```

### Questions posées pour trancher (au moment de la décision initiale)

1. Les CDR bruts sont-ils déjà archivés ailleurs (disque, autre système) ? → **Résolu depuis : oui, archivage sur serveur backup 7 jours après traitement.**
2. Le décodage sera-t-il fait une seule fois ou pourrait-il devoir être refait ? → **Réponse : pas sûr.**

### Raisonnement de décision

Au moment de la décision, l'incertitude sur l'archivage faisait pencher vers la prudence maximale : garder le brut en BDD comme filet de sécurité. Cette réserve est maintenant levée - l'archivage existe (serveur backup, rétention 7 jours). `qos_negotiated_raw` en BYTEA reste néanmoins utile **par commodité** : re-décoder sans aller rechercher un fichier sur le serveur backup, à un coût de stockage négligeable (quelques centaines d'octets par ligne, bien compressé par TimescaleDB sur les chunks anciens). Ce n'est plus un filet de sécurité indispensable, mais un confort opérationnel qui reste justifié.

### Décision finale (inchangée)

**Garder les deux colonnes** :
```sql
qos_negotiated_raw    BYTEA    -- confort : re-décoder sans aller chercher le backup
qos_qci, qos_arp       SMALLINT -- valeurs utilisées par l'algorithme au quotidien (étapes 5-7)
```

---

## 9. Diagramme de classes - ajustements (Couche 1-2 uniquement)

Les compléments des sections 4-6 ont été reportés sur le diagramme UML. Couches 3 à 5 (profils, détection, corrélation, output) **inchangées** - elles ne consomment qu'`ObservationHoraire.qos`, un scalaire unique.

- **`EnregistrementCDR`** : ajout de `type_cdr : str` ; `qos_arp` annoté `(PL 1-15)` ; `decoder_qos(octet_string)` scopé `· S-CDR` ; note `half_rate | hr_ratio (CS)` pour marquer le domaine voix ; référence TS scindée en deux notes distinctes - `S-CDR → octet-string (TS 29.274)` et `PGW/SGW → EPCQoSInformation (TS 29.212)`.
- **`CollecteurCDR`** : note mise à jour - `multi-sources · décodage selon type_cdr`.
- **`ObservationHoraire`** : ajout de `+calculer_qos()` et d'une note explicite `qos : règle d'agrégation à définir` *(résolu depuis, voir ci-dessous)*.

### Point ouvert - règle d'agrégation de `observation_horaire.qos` - RÉSOLU depuis

> Cette section documentait le principal point bloquant du projet à ce moment. **C'est résolu.**

La colonne `qos` existe déjà dans `observation_horaire`. Règle retenue : à chaque cycle horaire, les
`enregistrement_cdr` porteurs d'une QoS sur la période sont rassemblés en un vecteur d'indicateurs
individuels, sommé directement (pas un ratio - la proposition de "part de sessions dégradées" évoquée
plus bas dans ce document a été abandonnée). L'indicateur individuel retenu par CDR est le
`packet_error_loss_rate` de son QCI négocié (jointure sur `qci_caracteristique`, données normalisées
TS 23.203) plutôt qu'un seuil de "dégradation" arbitraire. Implémenté et testé de bout en bout dans
`AgregateurHoraire` (`src/agregation/agregateur_horaire.py`) - voir `enregistrement_cdr_revise.md` et
`README.md` pour le détail complet et le SQL exact.

```sql
COMMENT ON COLUMN observation_horaire.qos IS
    'QoS horaire agrégée depuis enregistrement_cdr : somme directe du vecteur d''indicateurs QoS
     individuels par CDR de la période. Règle définie - n''est plus un point bloquant.';
```

---

## 10. Premiers échantillons CDR réels obtenus (Togo)

Trois échantillons reçus à ce jour : JSON MOOV voix/SMS (`LOMBC1_*`, 13 288 enr.), CSV brut Nokia MSS YAS/Togocel (`Postmed_*_moc.dat`, 5 fichiers, 129 champs sans en-tête), et JSON MOOV **data/PGW** (`CMGa8_CCV_*_ber_csv_csv.json`, 4 487 enr., `cdrType = "gw"`). Constats vérifiés par inspection directe des fichiers, pas par hypothèse.

### 10.1 Échantillons voix (MOOV JSON + Nokia MSS YAS)

- Les deux sont **100% voix/SMS** (`cdrType` ∈ {smt, mtc, moc, smo} côté MOOV ; suffixe `_moc` côté Nokia). Aucun des deux n'est un CDR data.
- JSON MOOV : schéma à 30 champs nommés (`servedImsi`, `servedIsdn`, `duration`, `connectedCell`, etc.) ; **aucun champ QoS** (pas de QCI/ARP/half-rate) et **aucun champ forfait renseigné** (`typeForfait`, `tarifPlan` = null sur 100% des lignes).
- Nokia MSS YAS : CSV positionnel à 129 colonnes, **sans documentation de champs** - aucun champ ne peut être identifié avec certitude comme indicateur Full Rate/Half Rate sans le dictionnaire de champs de l'équipementier (quelques candidats positionnels repérés par analyse de cardinalité, mais non confirmés). *(Devenu sans objet depuis : décision validée de ne pas traiter les fichiers .dat Nokia MSS du tout, JSON/CSV uniquement - voir README.md.)*
- `connectedCell` est rempli à 100% côté MOOV (2625 cellules distinctes sur l'échantillon) → exploitable comme clé de jointure vers `congestion_horaire.cellule_id`. *(N'est plus applicable : `congestion_horaire` n'a plus de colonne `cellule_id` - le flux QUALCOP est désormais réseau entier, voir README.md.)*

### 10.2 Échantillon PGW/data (`cdrType = "gw"`)

- **Même schéma JSON à 30 champs** que l'échantillon voix MOOV - confirme un schéma unique côté MOOV, différencié par `cdrType`, pas un format par type de CDR.
- `uplink` et `downlink` sont **réellement peuplés et distincts** (40 à 475 837 392, médianes ~14 876 / ~27 018) - confirme que la distinction montant/descendant demandée par Mr. Ramzi existe déjà nativement dans le pipeline pour le data.
- `ratType` ∈ {'1', '2', '6'} (1035 / 87 / 3365 enr.) - cohérent avec un encodage TS 29.274 (UTRAN/GERAN/EUTRAN), **à confirmer** : je n'ai pas vérifié le mapping exact valeur→RAT contre la spec dans cette session.
- **Toujours aucun champ QoS** (ni QCI, ni ARP, ni MBR/GBR) et **toujours aucun champ forfait renseigné** (`typeForfait`/`tarifPlan`/`tarifForfait`/`montant`/`tarif` = null/0 sur 100% des lignes).
- `dataVolume` reste à 0 même quand `uplink`/`downlink` sont peuplés → pour le data, ce sont `uplink`/`downlink` qu'il faut utiliser, pas `dataVolume`.

**Conclusion transversale (3 échantillons, 2 opérateurs, voix + data) : le pipeline actuel ne porte aucun champ QoS et aucun lien vers le forfait, où que ce soit.** Ce n'est pas une lacune ponctuelle à un type de CDR - c'est structurel au JSON actuel, qui ne contenait que les champs déjà utilisés par l'équipe billing/traitement existante. *(Ce constat a depuis motivé la décision architecturale : le lien CDR→forfait ne viendra jamais du CDR réseau, voir `enregistrement_cdr_revise.md`.)*

### 10.3 Réponses de l'équipe lead (Ranim Damak / Baha Eddine Hmidi, 24/06/2026)

| Question posée | Réponse obtenue | Statut |
|---|---|---|
| Échantillon PGW disponible ? | Fourni (`cdrType="gw"`, section 10.2) | ✅ Résolu |
| YAS aura-t-il un JSON filtré équivalent à MOOV ? | Oui, **même structure**. Point d'implémentation : ne pas parser le JSON manuellement côté `CollecteurCDR`, le désérialiser en liste d'objets Java pour le traitement. | ✅ Résolu (+ note d'implémentation) |
| Les forfaits occasionnels ont-ils une période de validité trackée ? | Oui en principe - *« les forfaits ont tous une période de validité, le problème c'est comment détecter cela »*. Mécanisme de détection encore à définir, discussion détaillée prévue. | ⏳ Toujours ouvert (voir README.md) |

### 10.4 Proposition - champs à demander pour enrichir le JSON

Le résumé indique que l'équipe va ajouter au JSON les champs QoS nécessaires, sur demande. Sur la base du travail des sections 4-6, voici la liste concrète à transmettre :

**Pour `cdrType = "gw"` (PGW/data)** - champs `EPCQoSInformation` (TS 29.212, section 5) :
- `qci` (INTEGER)
- `arpPriorityLevel` (INTEGER 1-15) - et si possible `arpPci`/`arpPvi` (booléens)
- `mbrUplink` / `mbrDownlink`
- `gbrUplink` / `gbrDownlink`
- préciser l'unité utilisée par leur décodeur (bit/s ou kbps - TS 29.212 et TS 29.274 diffèrent, cf. section 5)

**Pour les `cdrType` voix (`moc`/`mtc`)** - équivalent de `TrafficChannel` (section 3) :
- un champ indiquant le mode full rate / half rate du canal radio (nom exact à leur charge, selon ce que leur décodeur expose)

**Pour tous types** - lien forfait : *(devenu sans objet - voir décision architecturale ci-dessus, ce lien ne viendra jamais du CDR réseau, quel que soit le champ disponible)*

### 10.5 `trafic_horaire` UL/DL - RÉSOLU depuis

> Section d'origine : `trafic_horaire.volume_data` (Famille 2) est une colonne unique, alors que l'échantillon PGW confirme que `uplink`/`downlink` sont des métriques natives et distinctes. Pour respecter l'exigence de Mr. Ramzi (« on doit vérifier uplink and downlink both »), `volume_data` devra probablement se scinder en `volume_data_ul` / `volume_data_dl`. **Pas encore appliqué** à l'époque.

**Décision prise depuis** : pas de scission. `volume_data` reste une colonne unique, **DL uniquement**
(le DL représente 90-92% du volume total, l'UL jugé non significatif pour l'algorithme). La distinction
`uplink`/`downlink` native au CDR (section 10.2) reste disponible au niveau `enregistrement_cdr` si
besoin d'une analyse plus fine, mais l'agrégat horaire `trafic_horaire.volume_data` ne la reprend pas.

---

## 11. Questions posées à l'époque - état actuel

**Résolues depuis la dernière version (déjà listées comme telles) :**
- ~~Opérateurs + nombre de forfaits~~ → MOOV + YAS, ~20-30 forfaits (15 data, 5-10 voix, 5-10 mixte).
- ~~Échantillon PGW disponible ?~~ → fourni et analysé (section 10.2).
- ~~YAS aura un JSON filtré équivalent à MOOV ?~~ → oui, même structure (section 10.3).
- ~~Archivage des CDR bruts~~ → confirmé, serveur backup à 7 jours.

**Résolues depuis (nouvelles, cette mise à jour) :**
- ~~#4 Full Rate/Half Rate côté Nokia MSS~~ → **sans objet** : décision validée de ne pas traiter les fichiers `.dat` Nokia MSS, JSON/CSV uniquement.
- ~~#5 Comment rattacher chaque CDR à un forfait~~ → **résolu architecturalement** : jamais depuis le CDR réseau, uniquement via `consommation_forfait` (flux SurePay).
- ~~#6 `observation_horaire.qos` règle d'agrégation~~ → résolue (voir section 9), somme directe du vecteur PELR par CDR.
- ~~#8 Congestion : agréger les cellules en un `C_k^d` unique~~ → **question devenue sans objet** : QUALCOP fournit désormais 3 valeurs réseau entier par heure (CS/PS/EPS), pas de découpage par cellule à agréger.
- ~~#13 Scission UL/DL de `trafic_horaire.volume_data`~~ → résolue, DL uniquement (section 10.5).

**Toujours ouvertes :**

1. Les CDR Togo seront-ils au même format que les échantillons Niger (ASN.1/BER, `ChangeOfCharCondition`, tag `[2]=qosNegotiated`), ou un format différent selon l'équipementier togolais ?
2. Quels types de nœud génèrent les CDR à traiter : PGW-CDR (4G) uniquement, ou aussi S-CDR / SGW-CDR (3G) et G-CDR (2G/GGSN) ? Seuls MSC-CDR et PGW-CDR ont été vus dans les échantillons réels reçus à ce jour.
3. Existe-t-il une documentation d'encodage propre à l'équipementier pour les champs MBR/GBR du `qosNegotiated` ? Les longueurs 12/15/17 octets observées (échantillons antérieurs) ne correspondent pas à TS 29.274 - quelle spec est réellement suivie ?
4. Unité des débits : TS 29.212 = bit/s, octet-string TS 29.274 = kbps - lequel le CDR utilise-t-il réellement ?
5. L'opérateur togolais utilise-t-il des QCI spécifiques opérateur (128-254) ? Si oui, quelle correspondance vers un niveau de QoS exploitable ?
6. Dispose-t-on de suffisamment d'historique au démarrage pour amorcer un premier profil statistique ? *(Nuance depuis : la fenêtre n'est plus fixée à 90 jours - `ModuleLissage` accepte une fenêtre de longueur libre, donc cette question porte maintenant sur la profondeur d'historique réellement disponible en prod, pas sur une contrainte de 90 jours à respecter.)*
7. Mécanisme de détection des forfaits occasionnels : confirmé qu'une période de validité existe, mais comment la détecter / où est-elle exposée ?
8. `hr_ratio` : comment l'agréger par heure à partir des flags half-rate des CDR voix ? *(Reformulé depuis : plus "par forfait, heure" puisque `enregistrement_cdr` ne porte plus de forfait - l'agrégation serait purement temporelle, vers `observation_horaire.half_rate`. Non implémenté à ce jour - `AgregateurHoraire` ne couvre que congestion + qos.)*
9. Opérateur associé au flux SurePay (billing) + catalogue forfait réel - **nouveau point ouvert, sans rapport avec le CDR réseau** : voir `README.md` décision n°2, qui bloque actuellement tout le volet trafic du pipeline.

---

## 12. Prochaines étapes (état à la rédaction de ce document - voir `README.md` pour l'état actuel)

| Étape | Statut (à l'époque) | Statut actuel |
|---|---|---|
| Architecture BDD complète (12 tables, 5 familles) | ✅ Confirmée par Mr. Ramzi | ✅ Chargée en base, testée, évoluée depuis (voir README.md) |
| Vérification QoS Negotiated vs spec actuelle | ✅ Terminée | Inchangé - reste valide |
| Classification PGW (data) vs MSC (voix) | ✅ Terminée | Inchangé - reste valide |
| EPCQoSInformation - champs vérifiés (TS 29.212) | ✅ Terminée | Inchangé - reste valide |
| Décodage hexadécimal → binaire (qosNegotiated) | ✅ Terminé, vérifié | Inchangé - reste valide |
| Mise à jour table `enregistrement_cdr` | ✅ Décidée | ✅ Intégrée au schéma final (voir `enregistrement_cdr_revise.md`) |
| Décision BYTEA | ✅ Confirmée | Inchangé |
| Règle d'agrégation `observation_horaire.qos` | ⏳ Ouverte | ✅ Résolue et testée (section 9) |
| Mécanisme forfaits occasionnels | ⏳ Confirmé en principe | ⏳ Toujours ouvert |
| Scission UL/DL de `trafic_horaire.volume_data` | ⏳ Proposée | ✅ Résolue : pas de scission, DL uniquement |
| Écriture du `schema.sql` final | ⏳ En attente | ✅ Fait, chargé en base, testé de bout en bout (congestion + QoS) |
| Collecteurs CDR/QUALCOP | Non démarré | ✅ `CollecteurCDR` testé (17 775 CDR) ; ✅ `CollecteurQualcop` (mock) testé |
| Collecteur billing (SurePay) | Non démarré | Codé, bloqué (opérateur SurePay + catalogue forfait en attente - voir README.md) |
| Agrégation horaire + profils QoS/congestion | Non démarré | ✅ Testés de bout en bout (`AgregateurHoraire`, `ModuleLissage`) |
| Détection d'anomalies, rapports | Non démarré | À faire |

---

## Annexe - Sources et méthode de vérification

- **PDF source** : `ts_132298v170400p.pdf`, confirmé via `pdfinfo` comme 3GPP TS 32.298 v17.4.0, Release 17, ETSI, 13 octobre 2022, 264 pages.
- **TS 29.212** : définitions AVP (qCI, Guaranteed-Bitrate, Allocation-Retention-Priority, APN-Aggregate-Max-Bitrate) vérifiées directement dans la spec (interface Gx).
- **Méthode** : extraction de texte page par page via `pdfplumber` (Python), recherche ciblée sur `QoSInformation` et `qosNegotiated`, lecture intégrale des pages 163 à 169 pour valider chaque champ et chaque commentaire mot pour mot. Décodeur hex→binaire écrit et exécuté pour vérifier l'exemple de la section 6.
- **Correction de trajectoire** : une première reconstruction du bloc ASN.1 (basée sur sources croisées Wireshark/Cisco) s'est révélée incomplète - elle ne listait que les champs `[1]` à `[9]` au lieu des 23 champs réels. La vérification directe dans le PDF a permis de produire la version exhaustive et exacte.
- **Note de vérification** : les réserves listées section 10 (notamment #3 sur les longueurs MBR/GBR non conformes à TS 29.274) ont été rédigées en cours d'analyse, pas par Mr. Ramzi - elles documentent les points où la spec générique ne suffit pas à expliquer les échantillons réels et nécessitent une confirmation côté équipementier/réseau.