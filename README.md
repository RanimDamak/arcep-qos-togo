# ARCEP - Impact des Forfaits sur les Trafics et la QoS (Togo)

Analyse de l'impact des forfaits mobiles sur le trafic réseau et la qualité de service (QoS), pour le compte de l'ARCEP (Togo). Pipeline : collecte de CDR réseau + billing → agrégation horaire → profils statistiques de référence → détection d'anomalies (règle 3σ) → rapports.

Ce fichier est la référence unique et à jour du projet (décisions, état du code, points bloquants).
`docs/QoS_BDD_ARCEP_resume.md` est un journal de décisions techniques (pourquoi certains choix ont été faits, y compris des pistes explorées puis abandonnées) - pas un état courant. En cas de doute sur l'état actuel, ce README fait foi.

## 1. Mise en route (TimescaleDB via Docker)

Le projet utilise **TimescaleDB** (extension PostgreSQL), pas une base PostgreSQL classique créée à la main. Voir `DEV_SETUP.md` pour la procédure complète ; résumé rapide :

```bash
docker run -d --name arcep-tsdb -p 6543:5432 -e POSTGRES_PASSWORD=arcep_dev -e POSTGRES_DB=arcep timescale/timescaledb-ha:pg18
docker cp schema.sql arcep-tsdb:/tmp/schema.sql
docker exec -it arcep-tsdb psql -U postgres -d arcep -f /tmp/schema.sql
```

Vérifier : `docker exec -it arcep-tsdb psql -U postgres -d arcep -c "SELECT hypertable_name FROM timescaledb_information.hypertables ORDER BY 1;"` - tu dois voir 8 hypertables + 3 tables normales = 11 tables au total, plus la vue `occurrence_forfait_jour` (voir §6). Les hypertables TimescaleDB gèrent leurs partitions ("chunks") en interne (`_timescaledb_internal._hyper_X_Y_chunk`) - elles ne sont pas listées dans `\dt` ni nommées par mois, contrairement à l'ancien schéma à partitionnement natif PostgreSQL (conservé en repli dans `archive/schema_native_postgres.sql`, non utilisé).

**Important - `pg_dump`/`psql` locaux (Windows) sont souvent en version antérieure à celle du conteneur** (ex. v16 local vs PG18 dans le conteneur) : Postgres refuse un dump si l'outil client est plus vieux que le serveur (`aborting because of server version mismatch`). Pour dump/reload, exécuter `pg_dump`/`psql` **depuis le conteneur**, pas depuis l'installation locale :

```bash
docker exec -e PGPASSWORD=arcep_dev arcep-tsdb pg_dump -U postgres -d arcep -F c -f /tmp/backup.dump
docker cp arcep-tsdb:/tmp/backup.dump .\backup.dump
```

Connexion dev : `host=localhost port=6543 dbname=arcep user=postgres password=arcep_dev` (conteneur nommé `arcep-tsdb`).

**Toutes les modifications de schéma appliquées en cours de route sur le conteneur dev sont répercutées dans `schema.sql` au fil de l'eau** (voir §6) - le fichier reste la source de vérité pour un rechargement à froid, ne jamais laisser diverger schéma live / fichier.

## 2. Mise en route (Python)

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows (cmd.exe)
pip install -r requirements.txt
copy .env.example .env
# éditer .env avec les paramètres de connexion ci-dessus (jamais commit ce fichier)
```

La configuration se fait via `.env` (lu par `src/donnees/db.py`), pas via `config/db_config.py` (mécanisme abandonné). Connexion exposée via le context manager `connexion()` (pas `get_connection()`) - commit automatique en sortie normale, rollback sur exception.

`openpyxl` est requis pour `Heatmap`/`Rapport` (export Excel) - vérifier sa présence dans `requirements.txt`.

## 3. Où mettre les échantillons reçus

Les fichiers réels vivent hors repo (ex. `Downloads/ARCEP_Forfaits_QoS/CDR_JSON_ARCEP_TOGO/...` en local, PAS dans le dossier du projet).

Fichiers reçus et leur attribution opérateur :

| Fichier | Opérateur | Contenu | Statut |
|---|---|---|---|
| `Moov MSC/LOMBC1_*.json` | **MOOV** (confirmé) | Voix+SMS (MSC) | Traité, 13 288 CDR chargés |
| `PGW/CMGa8_CCV_*.json` | **MOOV** (confirmé) | Data (PGW) | Traité, 4 487 CDR chargés (nom de fichier `.ber.csv.csv.json` : reliquat d'un pipeline de conversion, structure JSON réelle conforme, sans problème) |
| `Yas MSC/Postmed_Nokia_MSS_*.dat` | YAS (Togocel) | Voix (MSC) | **Non traité** - décision validée : format Nokia MSS hors périmètre, JSON/CSV uniquement |
| `SurePay/doc_elasticsearch_forfait_TG.json`, `_TG_2.json` | YAS | Billing (achats, `cdrType="forfait"`) | Traité, ignorés volontairement (montant/solde non stockés) |
| `SurePay/doc_elasticsearch_forfait_TG_3.json` (code `847`) | YAS | Billing (consommation voix) | **Traité et inséré** - code absent du catalogue reçu, stub auto-créé (`forfait.connu=FALSE`, voir §6) |
| `SurePay/doc_elasticsearch_forfait_TG_4.json` (code `892`) | YAS (`_index="togocel-001179"` confirmé) | Billing (consommation voix) | **Traité et inséré** - même situation que `TG_3` |
| `forfait_configuration.csv` (392 lignes, Baha) | YAS | Catalogue forfait officiel | **Seedé en base** (392 lignes, `forfait.operateur_id='YAS'`) |
| `forfait_configuration_moov.csv` / `forfait_counter.csv` / `forfait_config_counter.csv` (Baha) | MOOV | Catalogue forfait officiel (structure composite, plusieurs compteurs par forfait) | **Nettoyé et prêt** (`forfait_configuration_moov_clean.csv`, 163 lignes dédupliquées depuis 185 lignes brutes - doublons environnement dsitest/hxc supprimés, codes réellement multi-produits désambiguïsés par id composite) - **pas encore seedé, scope de mise en service non confirmé** (voir §7 point 2) |

Chaque fichier SurePay ne contient qu'**un seul événement** à sa racine (confirmé par l'équipe) - `CollecteurBilling` s'exécute donc une fois par fichier, pas une fois pour un lot.

## 4. Architecture (5 couches du diagramme de classes + structure réelle)

arcep-qos-togo/
├── archive/ # schema_native_postgres.sql (ancien schéma à partitionnement natif, non utilisé)
├── data/
│ ├── exports/ # sorties générées (rapports, etc. - vide dans le repo, .gitkeep)
│ └── samples/ # PAS de CDR réels ici (voir §3) - vide dans le repo, .gitkeep
├── docs/ # QoS_BDD_ARCEP_resume.md - journal de décisions techniques, pas un état courant (§ intro)
├── src/
│ ├── collecte/ # Couche 1 - CollecteurCDR, CollecteurQualcop, CollecteurBilling, ingestion.py,
│ │ # seed_forfait.py, qos_decoder.py (décodage TS 24.008, voir §6),
│ │ # generer_mock_consommation.py, generer_mock_observation.py,
│ │ # injecter_spike_mock.py, injecter_spike_qos_mock.py (scripts de test/mock)
│ ├── donnees/ # Couche 2 - db.py (connexion). Les objets données (TraficHoraire,
│ │ # CongestionHoraire, EnregistrementCDR, ConsommationForfait,
│ │ # ObservationHoraire, Forfait, Operateur) sont les TABLES du schéma
│ │ # (schema.sql), pas des classes Python séparées à ce jour.
│ ├── profils/ # Couche 3 - vide à ce jour ; ProfilTrafic/ProfilQoS sont couverts par
│ │ # ModuleLissage (src/agregation/), pas des classes séparées.
│ ├── detection/ # Couche 4 - DetecteurAnomalie, AlerteQoS, AnalyseurCorrelation
│ ├── agregation/ # AgregateurHoraire, AgregateurTrafic, ModuleLissage - voir note ci-dessous
│ └── output/ # Couche 5 - Heatmap, Rapport (Excel) ; Historique / export PDF non faits
└── tests/ # test_collecteur_cdr.py (12 tests, dont 5 couvrant le décodage QoS TS 24.008 -
# forfait_id retiré des assertions, champ jamais présent dans LigneCDR)


**Note sur `src/agregation/`** : ce dossier ne fait pas partie des 5 couches nommées dans le diagramme de classes d'origine - `ModuleLissage` y est rangé alors que le diagramme le place dans la Couche 4 (`detection/`). Choix assumé, inchangé depuis la version précédente de ce README : regrouper toute la logique d'agrégation/calcul de profils dans un seul dossier plutôt que de suivre strictement le découpage du diagramme.

## 5. État détaillé par composant

| Composant | Statut | Détail |
|---|---|---|
| Schéma BDD (`schema.sql`) | ✔ Terminé | 11 tables + 1 vue, 5 familles. Voir §6 pour le détail des évolutions. |
| `CollecteurCDR` | ✔ Terminé | Testé de bout en bout (real : 4 487 CDR PGW, 0 erreur ; mock historique : 17 775 CDR). Modes réel et `--mock`. QoS data décodée via TS 24.008 (`qosNegotiated`, voir §6) ; QoS voix non décodée, portée par `half_rate`/`hr_ratio` (pas encore peuplés, source ne fournit pas encore ce champ - même situation que `qosNegotiated` avant son ajout). **Attention** : `--mock` ne mocke QUE les champs QoS, jamais les timestamps (dérivés de `dateTime` du fichier) - ne permet pas de cibler une date synthétique arbitraire. |
| `CollecteurQualcop` | ✔ Terminé (mock uniquement) | Inchangé. |
| `CollecteurBilling` | ✔ Terminé, validé sur les 4 échantillons réels | **Débloqué** : catalogue YAS seedé, codes inconnus (`847`/`892`) auto-provisionnés (`forfait.connu=FALSE`) plutôt que rejetés en FK. Accepte `--operateur` (défaut `YAS`). |
| `AgregateurHoraire` | ✔ Terminé | Inchangé, validé 2025-08-01 17h/18h. |
| `AgregateurTrafic` | ✔ Terminé, validé (réel + mock) | Débloqué par le seed du catalogue. `est_mock` propagé depuis `consommation_forfait` via `bool_or`. |
| `ModuleLissage` (QoS + trafic) | ✔ Terminé, validé sur 2 jours | Volet trafic implémenté (`calculer_profil_trafic`) : calcule **alpha** (part de trafic, PAS le volume brut - correction appliquée le jour de cette mise à jour, voir §6). `sigma_val`/`hr_sigma` etc. nullable (historique 1 jour). |
| `DetecteurAnomalie` | ✔ Terminé, validé (détection positive confirmée) | Étapes 4-5 (règle 3σ sur alpha). Ignore les couples sans profil suffisant (`sigma_val NULL`), jamais traité comme "normal" par défaut. |
| `AlerteQoS` | ✔ Terminé, validé (3 indicateurs confirmés) | Étape 6, règle 3σ **à sens unique** sur C/QoS/HR (voir §6 - écart assumé avec le document technique). |
| `AnalyseurCorrelation` | ✔ Terminé, validé mécaniquement | Étape 7, vraie corrélation de Pearson (`CORR()`), gardée par `MIN_POINTS=3`. **Limite connue** : avec 3 jours d'historique seulement, les valeurs de r ne sont pas statistiquement significatives - à interpréter avec prudence tant que la fenêtre glissante réelle (des semaines/mois) n'est pas accumulée. |
| `Heatmap` | ✔ Terminé, validé | Matrice n×24, export `.xlsx`, couleurs par niveau. |
| `Rapport` (Excel) | ✔ Terminé, validé | 4 feuilles (Heatmap, Anomalies, Alertes QoS, Corrélations), par opérateur, journalisé dans `rapport`. Alertes QoS **non filtrées par opérateur** (réseau entier, voir §6). |
| `Rapport` (PDF) | À faire | Non commencé - `rapport.chemin_pdf` reste NULL. |
| `Historique` (Couche 5) | À faire | Non commencé. |

## 6. Décisions - résolues (avec justification)

*(décisions historiques inchangées : agrégation `observation_horaire.qos`, format `congestion_horaire`, Nokia MSS hors périmètre, `volume_data` DL uniquement, `trafic_total` non stocké, attribution opérateur CDR réseau - voir version précédente si besoin du détail, non reproduites ici pour rester lisible)*

**Nouvelles décisions du jour :**

- **`forfait.type` supprimé** : décision du responsable projet - le type (voix/data/mixte) n'est pas nécessaire à l'algorithme ni aux rapports. Colonne retirée (`ALTER TABLE forfait DROP COLUMN type`), plus de CHECK associé.

- **`forfait` : `nom` nullable, `connu BOOLEAN DEFAULT TRUE` ajouté** : gère les codes forfait rencontrés en trafic mais absents du catalogue reçu (ex. `847`/`892`, absents des 392 lignes YAS). `CollecteurBilling` auto-provisionne un stub (`nom=NULL, connu=FALSE`) plutôt que de rejeter la ligne en FK - ces codes peuvent eux-mêmes être des forfaits occasionnels impactant la QoS, donc jamais exclus du pipeline. Fréquence réelle de ces codes consultable via la vue `occurrence_forfait_jour` (dérivée de `consommation_forfait`, pas de table dédiée - décision alignée sur la philosophie déjà en place pour `trafic_total`/`jour`/`heure` non stockés).

- **`consommation_forfait.est_mock` / `trafic_horaire.est_mock` ajoutés** : mêmes conventions que `congestion_horaire.est_mock`. Propagation trafic → `bool_or(est_mock)` dans `AgregateurTrafic` (une ligne agrégée est mock si au moins une ligne source l'est).

- **`profil_trafic_snapshot.sigma_val` rendu nullable** : incohérence corrigée avec `profil_qos_snapshot` (même cas - un seul jour d'historique = écart-type indéfini, NULL est le comportement correct, pas une erreur).

- **`calculer_profil_trafic` calcule alpha, pas le volume brut** : bug corrigé - la première implémentation faisait `AVG(volume)` au lieu de `AVG(volume_forfait / volume_total_heure)`. Le document technique (§4.2-4.3) définit explicitement le profil sur la **part de trafic**, pas le volume absolu - une comparaison 3σ sur du volume brut n'a pas de sens (bruitée par la croissance/décroissance naturelle du trafic total).

- **`AlerteQoS` : les 3 indicateurs (C, QoS, HR) sont tous à sens unique (dégradation uniquement)** : le document technique décrit la règle QoS comme un intervalle bilatéral (`∉ I₃`), traité comme une incohérence de rédaction - on ne cherche jamais une "QoS anormalement bonne", et les 2 autres indicateurs sont clairement à sens unique. Décision alignée sur les 3 indicateurs.

- **`AnalyseurCorrelation` : vraie corrélation de Pearson (`CORR()`) dès maintenant, pas de placeholder** : contrairement au catalogue forfait/CDR réels (données externes manquantes), la faible profondeur d'historique se résout naturellement en prod (fenêtre glissante réelle) - donc le calcul réel est écrit dès maintenant, gardé par `MIN_POINTS` plutôt que remplacé par une logique jetable à réécrire plus tard.

- **`Rapport` : Alertes QoS non filtrées par opérateur** : `observation_horaire`/`congestion_horaire` sont réseau entier (QUALCOP ne distingue pas les opérateurs) - contrairement aux anomalies/corrélations, attachées à un `forfait_id` donc à un opérateur via `forfait.operateur_id`. La feuille "Alertes QoS" du rapport porte un avertissement explicite à ce sujet.

**Nouvelles décisions (format QoS réel) :**

- **Format QoS réel identifié : TS 24.008, pas TS 29.274/EPC** : les CDR portent l'IE QoS PDP context historique (§10.5.6.5, 15/16 octets - Delay class, Precedence class, Traffic class, débits, BER, SDU error ratio, Transfer delay, Traffic Handling Priority, Signalling Indication), pas le profil EPC (QCI/ARP/PCI/PVI, 22+ octets) supposé initialement. `qos_qci`/`qos_arp`/`qos_pci`/`qos_pvi` et la table `qci_caracteristique` retirés du schéma (jamais réellement câblés par le pipeline de détection - confirmé avant suppression). Remplacés par `delay_class_score`, `precedence_class_score`, `max_bitrate_ul_kbps`, `max_bitrate_dl_kbps`, `residual_ber_score`, `sdu_error_ratio_score`, `transfer_delay_ms`/`transfer_delay_score`, `traffic_handling_priority_score`, `signalling_indication`, décodés depuis `qos_negotiated_raw` par `src/collecte/qos_decoder.py`. Détail complet du décodage : voir `Analyse_QoS_15octets.md`.

- **Alignement d'octets de `qosNegotiated` confirmé empiriquement** : le premier octet de la chaîne n'appartient PAS à l'IE TS 24.008 - l'octet 3 réel de la spec commence au 2e octet. Confirmé sur 4 échantillons réels distincts : Traffic Class décode systématiquement sur une valeur définie et cohérente avec `rATType` sous cette hypothèse, jamais sous la lecture directe. Origine exacte de cet octet 0 non identifiée, conservé tel quel (non décodé) dans `qos_negotiated_raw`.

- **QoS décodée uniquement côté data (S-CDR/PGW-CDR/SGW-CDR), jamais côté voix (MSC-CDR)** : `CollecteurCDR` ne tente même pas le décodage sur un CDR voix, même si `qosNegotiated` apparaissait par erreur dans le JSON. La qualité voix reste portée par `half_rate`/`hr_ratio` (non encore peuplés par la source à ce jour).

- **Traffic Class (octet 6) différé** : décision du responsable projet - pas de colonne pour le moment. Conséquence acceptée : `transfer_delay_ms`/`transfer_delay_score`/`traffic_handling_priority_score`/`signalling_indication` sont décodés **sans filtre de validité** lié à la classe de trafic (la spec les dit "ignorés" selon certaines classes - bruit potentiel accepté plutôt que de bloquer).

- **Catalogue Moov nettoyé (`transaction_code` seul insuffisant comme clé)** : sur 185 lignes reçues, seuls 96 `transaction_code` sont uniques - 21 doublons purs environnement (dsitest/hxc, même produit) et 6 codes réellement multi-produits (ex. `SUBSINTERNET` couvre 23 forfaits distincts). Catalogue dédupliqué à 163 lignes : `transaction_code` utilisé comme id quand unique, sinon id composite `transaction_code-<id_ligne_csv>` (ex. `SUBSINTERNET-109`) pour rester sous `forfait.id VARCHAR(30)`. Script prêt (`seed_moov_forfait.sql`), **pas encore exécuté** - voir §7 point 2.

## 7. Décisions encore ouvertes

| # | Sujet | Détail | Bloque |
|---|---|---|---|
| 1 | `consommation_forfait.cdr_type` côté data | Seul `"voiceCall"` confirmé pour la voix ; décision d'équipe : tout `cdr_type` ≠ `"voiceCall"` est traité comme data par élimination (déjà implémenté ainsi dans `CollecteurBilling`/`AgregateurTrafic`) - **considéré comme suffisant pour avancer**, ne bloque plus rien, mais la valeur exacte reste inconnue si jamais un comportement différent est observé | Rien à ce jour |
| 2 | Catalogue Moov - scope de mise en service | Catalogue nettoyé et prêt (163 lignes, voir §6) - **`seed_moov_forfait.sql` pas encore exécuté, date/scope de mise en service Moov non confirmé** | Rapport/détection pour MOOV (0 donnée MOOV dans le pipeline à ce jour) |
| 3 | Identification opérateur dans le JSON SurePay | Aucun champ explicite aujourd'hui (`_index` absent sur `TG_3`, présent sur `TG_4` - incohérence non expliquée) ; `operatorName` arrive "avec les mises à jour" côté équipe, sans ETA. `CollecteurBilling --operateur` suppose un seul opérateur par fichier en attendant (défaut `YAS`) | Ingestion multi-opérateur automatique du flux SurePay |
| 4 | Détection des forfaits occasionnels (période de validité) | Mécanisme de validité pas défini ; en pratique géré indirectement via `forfait.connu=FALSE` + vue `occurrence_forfait_jour`, mais pas de notion de date de début/fin de validité d'un forfait | Rien de bloqué à ce jour, amélioration possible |
| 5 | `observation_horaire` sans colonne `est_mock` | Contrairement à `congestion_horaire`/`trafic_horaire`/`consommation_forfait`, aucun moyen de distinguer une ligne mock d'une ligne réelle une fois insérée dans `observation_horaire` (dépend d'`AgregateurHoraire`, non revu aujourd'hui) | Traçabilité seulement, pas un blocage fonctionnel |
| 6 | `tarifPlan` (ex. `"1341"`) vu dans les échantillons SurePay | Pertinence pour le projet non confirmée par l'équipe | Rien à ce jour |
| 7 | Rapport PDF | Non commencé | Livrable "rapport complet" si le PDF est requis en plus de l'Excel |
| 8 | Traffic Class (octet 6, TS 24.008) | Différé - décision du responsable projet, à traiter plus tard | Bruit potentiel sur `transfer_delay_*`/`traffic_handling_priority_score`/`signalling_indication` (décodés sans filtre de validité en attendant, voir §6) - pas un blocage fonctionnel aujourd'hui |
| 9 | `forfait.id` clé simple (pas composite avec `operateur_id`) | Pas de risque de collision aujourd'hui (codes Moov toujours alphanumériques, codes YAS toujours numériques) mais aucune protection au niveau schéma si ça change un jour | Rien à ce jour, amélioration possible |

## 8. Ce qui a été vérifié de bout en bout

**Chaîne QoS/congestion** (2025-08-01 + 2025-07-31, 17h-19h, réel + mock) :
1. `CollecteurCDR --mock` → 17 775 CDR (13 288 Moov MSC + 4 487 PGW), 0 erreur.
2. `CollecteurQualcop` → congestion mock, 2 jours.
3. `AgregateurHoraire` → `observation_horaire` peuplée, valeurs vérifiées manuellement cohérentes.
4. `ModuleLissage` (volet QoS) → `profil_qos_snapshot` peuplé sur 2 jours, `c_sigma`/`qos_sigma`/`hr_sigma` non-NULL.
5. `AlerteQoS` → pic injecté (`injecter_spike_qos_mock.py`) sur 2025-08-02 17h, **3 alertes détectées** (C/QoS/HR), scores > seuils confirmés.

**Chaîne trafic/forfait** (mock + réel mêlés, correctement distingués via `est_mock`) :
1. `forfait` seedée : 392 lignes YAS (`seed_forfait.py`).
2. `CollecteurBilling` sur les 4 échantillons SurePay réels : achats ignorés (2), consommations insérées (2, codes `847`/`892` auto-stubbés `connu=FALSE`).
3. `AgregateurTrafic` sur données réelles (2026-07-07) et mock (2025-07-31/08-01, `generer_mock_consommation.py`) → `trafic_horaire` peuplée, `est_mock` propagé correctement.
4. `ModuleLissage` (volet trafic, formule **alpha** corrigée) → `profil_trafic_snapshot` peuplé sur 2 jours, alpha vérifié sommant à 1 par heure.
5. `DetecteurAnomalie` → pic injecté (`injecter_spike_mock.py`, forfait `109`) sur 2025-08-02 17h, **1 anomalie détectée** (niveau 2, alpha=0.889 vs seuil ~0.62).
6. `AnalyseurCorrelation` → 3 corrélations calculées (forfait `109` × C/QoS/HR), `r` entre 0.89 et 0.99, `est_causal=TRUE`.
7. `Heatmap` + `Rapport` → `rapport_YAS_2025-08-02.xlsx` généré et vérifié (4 feuilles), loggé dans la table `rapport`.

**Non testé / non fait** : catalogue Moov (nettoyé, prêt, pas encore seedé), export PDF, `Historique`.

**QoS TS 24.008 (session ultérieure)** :
1. `qos_decoder.py` validé sur 4 échantillons `qosNegotiated` réels distincts (ASN.1 `GPRSRecord`/`egsnPDPRecord`) - alignement d'octets confirmé empiriquement (voir §6), tous les champs décodent sur des valeurs définies, cohérence croisée avec `rATType` confirmée.
2. `migration_qos_ts24008.sql` exécutée sur le conteneur dev réel : `qci_caracteristique` confirmé absent (`SELECT tablename FROM pg_tables ...` → 0 ligne), `enregistrement_cdr` confirmé avec les 10 nouvelles colonnes QoS et sans `qos_qci`/`qos_arp`/`qos_pci`/`qos_pvi` (`\d enregistrement_cdr` vérifié).
3. `CollecteurCDR` (code mis à jour) réexécuté en mode réel sur le fichier PGW (`CMGa8_CCV_*.json`) contre la vraie base : **4 487 lus, 4 487 insérés, 0 ignoré** - identique au comptage historique. `qos_spec_version='absent'` confirmé pour les 4 487 lignes (normal, cet échantillon ne porte pas encore `qosNegotiated`) - chemin `psycopg`/`COPY` validé de bout en bout avec le nouveau schéma, pas seulement en tests unitaires.
4. Suite `tests/test_collecteur_cdr.py` : 12/12 tests passent, dont 5 nouveaux couvrant le décodage réel, le hex malformé (dégrade proprement sans crasher le fichier), et le fait qu'un CDR voix n'est jamais décodé QoS (même avec `--mock`). Corrige au passage un bug préexistant (`test_mapper_data_gw` référençait `forfait_id`, champ qui n'a jamais existé dans `LigneCDR`).

## 9. Commandes utiles (cheat-sheet)

```bash
# --- Collecte ---
python -m src.collecte.collecteur_cdr "<chemin_fichier.json>" --mock
python -m src.collecte.collecteur_qualcop --debut 2025-08-01T00:00:00 --fin 2025-08-02T00:00:00
python -m src.collecte.seed_forfait "<chemin_csv>" [operateur_id par defaut YAS]
python -m src.collecte.collecteur_billing "<chemin_fichier_surepay.json>" --operateur YAS

# --- Agregation / profils ---
python -m src.agregation.agregateur_horaire --debut 2025-08-01T00:00:00 --fin 2025-08-02T00:00:00
python -m src.agregation.agregateur_trafic --debut 2025-08-01T00:00:00 --fin 2025-08-02T00:00:00
python -m src.agregation.module_lissage --bucket-ref 2025-08-02 --fenetre-debut 2025-08-01T00:00:00 --fenetre-fin 2025-08-02T00:00:00

# --- Detection ---
python -m src.detection.detecteur_anomalie --debut 2025-08-02T17:00:00 --fin 2025-08-02T18:00:00 --bucket-ref 2025-08-02
python -m src.detection.alerte_qos --debut 2025-08-02T17:00:00 --fin 2025-08-02T18:00:00 --bucket-ref 2025-08-02
python -m src.detection.analyseur_correlation --debut 2025-08-02T17:00:00 --fin 2025-08-02T18:00:00 --fenetre-debut 2025-07-31T00:00:00 --fenetre-fin 2025-08-02T18:00:00

# --- Output ---
python -m src.output.heatmap --jour 2025-08-02 --sortie heatmap.xlsx
python -m src.output.rapport --jour 2025-08-02 --operateur YAS
```