-- ======================================================================================================================================
-- ARCEP - Impact des Forfaits sur les Trafics et la QoS
-- schema_timescaledb.sql - Schéma TimescaleDB (12 tables, 5 familles)
--
-- Cible : PostgreSQL 18 + TimescaleDB (prod). Dev : conteneur Docker identique (voir DEV_SETUP.md).
--
-- ======================================================================================================================================

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ============================================================================
-- FAMILLE 1 - Données de référence (tables normales, PAS des hypertables)
-- ============================================================================

CREATE TABLE operateur (
    id      VARCHAR(20)  PRIMARY KEY,          -- ex: 'MOOV', 'YAS'
    nom     VARCHAR(100) NOT NULL
);

CREATE TABLE forfait (
    id            VARCHAR(30)  PRIMARY KEY,                          -- code numérique (typeForfait des événements de consommation, ex. '892')
    nom           VARCHAR(150),                                     -- NULLABLE : NULL si code inconnu (connu=FALSE)
    operateur_id  VARCHAR(20)  NOT NULL     REFERENCES operateur(id),
    connu         BOOLEAN      NOT NULL DEFAULT TRUE                 -- FALSE si code auto-cree par CollecteurBilling (vu en trafic, absent du catalogue recu)
);

COMMENT ON TABLE forfait IS
    'Pas de colonne de validité (date_debut/date_fin) à ce jour : mécanisme de validité à définir.'
    'Ne pas ajouter de colonne avant clarification.';

COMMENT ON COLUMN forfait.id IS
    'Code numérique (ex. ''892''), référencé par consommation_forfait.forfait_id / trafic_horaire.forfait_id / '
    'profil_trafic_snapshot.forfait_id / evenement_detecte.forfait_id. NE PAS confondre avec le nom commercial (colonne nom).';

COMMENT ON COLUMN forfait.nom IS
    'Nom commercial (ex. ''Lema200+''), tel qu''il apparaît dans les événements d''achat SurePy (cdrType=''forfait''). '
    'Usage affichage/rapports uniquement - jamais utilisé pour une jointure.';

INSERT INTO operateur (id, nom) VALUES
    ('MOOV', 'Moov Africa Togo'),
    ('YAS',  'Togocel (YAS)');

-- ============================================================================
-- FAMILLE 2 - Observations brutes (hypertables)
-- ============================================================================

-- Agrégat horaire de trafic par forfait (source : TRAFSCAN).
-- bucket = début de l'heure. Un enregistrement par (bucket, forfait_id).
CREATE TABLE trafic_horaire (
    bucket          TIMESTAMPTZ NOT NULL,                       -- début de l'heure (partition key)
    forfait_id      VARCHAR(30) NOT NULL REFERENCES forfait(id),
    appels_voix_fr  FLOAT       DEFAULT 0,
    volume_voix_fr  FLOAT       DEFAULT 0,
    appels_voix_hr  FLOAT       DEFAULT 0,
    volume_voix_hr  FLOAT       DEFAULT 0,
    volume_data     FLOAT       DEFAULT 0,
    PRIMARY KEY (bucket, forfait_id)
);
SELECT create_hypertable('trafic_horaire', by_range('bucket', INTERVAL '7 days'));
CREATE INDEX idx_trafic_forfait ON trafic_horaire (forfait_id, bucket DESC);

COMMENT ON COLUMN trafic_horaire.volume_data IS
    'Downlink uniquement (DL). UL non retenu : DL représente 90-92% du volume total, jugé suffisant pour l''algorithme (décision validée).';

COMMENT ON TABLE trafic_horaire IS
    'jour/heure NE sont PAS stockées : calculer à la lecture via bucket::date / EXTRACT(hour FROM bucket). '
    'trafic_total NON stocké (recalculable = somme des volumes) - optimisation coût. '
    'Alimentée par agrégation de consommation_forfait (SUM voix/data groupé par bucket, forfait_id, '
    'WHERE forfait_id IS NOT NULL) en attendant le branchement direct de TRAFSCAN.';


-- Congestion RESEAU ENTIER par heure et par domaine (source : QUALCOP, un JSON par heure avec 3 valeurs - pas de découpage par cellule, décision validée).
-- Une ligne = un type de congestion (jamais 3 valeurs mélangées) : CS (voix, MSC), PS (data 3G), EPS (data 4G)
CREATE TABLE congestion_horaire (
    bucket           TIMESTAMPTZ NOT NULL,
    type_congestion  VARCHAR(3)  NOT NULL    CHECK (type_congestion IN ('CS', 'PS', 'EPS')),
    valeur           FLOAT       NOT NULL    CHECK (valeur >= 0 AND valeur <= 1),   -- fraction (0-1, PAS 0-100); généralement < 0.1 mais la borne haute
                                                                                    -- reste 1 - décision validée, pas de risque de rejeter un pic réel.
    est_mock         BOOLEAN     NOT NULL DEFAULT FALSE,    -- TRUE si généré par CollecteurQualcop (mock), jamais confondu avec un échantillon QUALCOP réel. Remplace
                                                            -- le marquage par cellule_id (préfixe MOCK-), qui n'existe plus depuis la suppression de cette colonne.
    PRIMARY KEY (bucket, type_congestion)
);
SELECT create_hypertable('congestion_horaire', by_range('bucket', INTERVAL '7 days'));

COMMENT ON COLUMN congestion_horaire.type_congestion IS
    'CS = domaine voix (MSC) ; PS = domaine data 3G ; EPS = domaine data 4G.';

COMMENT ON TABLE congestion_horaire IS
    'Réseau entier, PAS de découpage par cellule (décision validée - le flux QUALCOP fournit un JSON par heure avec exactement 3 valeurs, une par domaine).'
    'Structure prête (typée CS/PS/EPS, échelle 0-1) ;  aucun échantillon QUALCOP réel reçu à ce jour - alimentée en mock (est_mock=TRUE) en attendant.';

-- CDR individuels RÉSEAU (source : CollecteurCDR, MSC/PGW). Grain ÉVÉNEMENT : 1 ligne = 1 CDR.
-- Table 100% QoS, ne porte JAMAIS le forfait (structurel, pas une limite d'échantillon) : le forfait vient exclusivement de consommation_forfait (flux billing SurePay), voir plus bas.
CREATE TABLE enregistrement_cdr (
    id                  BIGSERIAL,
    cdr_timestamp       TIMESTAMPTZ NOT NULL,                                        -- instant précis (ex. champ "dateTime") - partition key
    cdr_reference       VARCHAR(50),                                                 -- ex. champ "cdrReference"
    type_cdr            VARCHAR(10) NOT NULL    CHECK (type_cdr IN ('S-CDR', 'PGW-CDR', 'SGW-CDR', 'MSC-CDR')),
    uplink              FLOAT,                                                       -- data : volume montant (octets), natif
    downlink            FLOAT,                                                       -- data : volume descendant (octets), natif
    qos_negotiated_raw  BYTEA,                                                       -- octet-string brut qosNegotiated (TS 24.008 §10.5.6.5, S-CDR/data uniquement)
    delay_class_score               SMALLINT CHECK (delay_class_score BETWEEN 1 AND 4),               -- Delay class (octet 3) - 1=meilleur, 4=pire ("best effort" = pas d'engagement)
    precedence_class_score          SMALLINT CHECK (precedence_class_score BETWEEN 1 AND 3),           -- Precedence class (octet 4) - 1=High priority, 3=Low priority
    max_bitrate_ul_kbps             INTEGER,                                                           -- débit montant max négocié, kbps (octet 8)
    max_bitrate_dl_kbps             INTEGER,                                                           -- débit descendant max négocié, kbps (octet 9)
    residual_ber_score              SMALLINT CHECK (residual_ber_score BETWEEN 1 AND 9),                -- Residual BER (octet 10, bits 8-5) - rang de sévérité réelle, 1=meilleur, 9=pire
    sdu_error_ratio_score            SMALLINT CHECK (sdu_error_ratio_score BETWEEN 1 AND 7),            -- SDU error ratio (octet 10, bits 4-1) - rang de sévérité réelle, 1=meilleur, 7=pire
    transfer_delay_ms                INTEGER,                                                           -- Transfer delay décodé en ms (octet 11, bits 8-3)
    transfer_delay_score             SMALLINT CHECK (transfer_delay_score BETWEEN 1 AND 3),
    traffic_handling_priority_score  SMALLINT CHECK (traffic_handling_priority_score BETWEEN 1 AND 3),  -- octet 11, bits 2-1
    signalling_indication            BOOLEAN,                                                           -- octet 14, bit 5 - métadonnée descriptive, hors score composite
    qos_spec_version    VARCHAR(10) DEFAULT 'absent',
    half_rate           BOOLEAN     DEFAULT FALSE,                                   -- domaine CS/MSC (voix)
    hr_ratio            FLOAT       DEFAULT 0,
    PRIMARY KEY (cdr_timestamp, id)
);
SELECT create_hypertable('enregistrement_cdr', by_range('cdr_timestamp', INTERVAL '1 day'));
CREATE INDEX idx_cdr_reference  ON enregistrement_cdr (cdr_reference);
CREATE INDEX idx_cdr_type       ON enregistrement_cdr (type_cdr, cdr_timestamp DESC);

COMMENT ON COLUMN enregistrement_cdr.type_cdr IS
    'Échantillon PGW réel : cdrType="gw" générique, mappé par défaut sur ''PGW-CDR'' - à confirmer.';

COMMENT ON COLUMN enregistrement_cdr.qos_negotiated_raw IS
    'Octet-string brut qosNegotiated (TS 24.008 §10.5.6.5, 15/16 octets, PAS le format EPC 29.212/29.274). Octet 0 = octet hors-spec non identifié, voir src/collecte/qos_decoder.py. NULL pour CDR voix (MSC-CDR).';

COMMENT ON COLUMN enregistrement_cdr.qos_spec_version IS
    'Traçabilité : ''24.008''=décodé réel (CDR data), ''MOCK''=--mock (CDR data), ''absent''=champ pas encore fourni par la source, ''voix''=CDR voix (qualité via half_rate/hr_ratio, pas qosNegotiated).';

COMMENT ON TABLE enregistrement_cdr IS
    'jour/heure NE sont PAS stockées : calculer à la lecture via cdr_timestamp::date / EXTRACT(hour FROM cdr_timestamp). Traffic Class (octet 6) différé (décision projet) - transfer_delay_*/traffic_handling_priority_score/signalling_indication sont décodés sans filtre de validité lié à la classe de trafic.';

-- Événements de consommation (appel/session data), source SurePay (billing), déjà attribués à un forfait par le système de facturation lui-même.
CREATE TABLE consommation_forfait (
    id            BIGSERIAL,
    bucket        TIMESTAMPTZ NOT NULL,                             -- dateTime de l'événement - partition key
    served_isdn   VARCHAR(20) NOT NULL,                             -- numéro abonné (format billing, 8 chiffres)
    forfait_id    VARCHAR(30)             REFERENCES forfait(id),   -- NULLABLE : NULL si débité directement du solde principal
    cdr_type      VARCHAR(20) NOT NULL,                             -- valeur brute source (ex. 'voiceCall') - pas de CHECK pour l'instant
    duration      INTEGER,                                          -- secondes, rempli côté voix
    data_volume   FLOAT,                                          -- octets, rempli côté data
    est_mock      BOOLEAN     NOT NULL DEFAULT FALSE,
    PRIMARY KEY (bucket, id)
);
SELECT create_hypertable('consommation_forfait', by_range('bucket', INTERVAL '1 day'));
CREATE INDEX idx_conso_forfait          ON consommation_forfait (forfait_id, bucket DESC);
CREATE INDEX idx_conso_served_isdn      ON consommation_forfait (served_isdn, bucket DESC);

COMMENT ON TABLE consommation_forfait IS
    'Grain événement (comme enregistrement_cdr), source SurePay. Alimente trafic_horaire par agrégation horaire. Pas de colonne montant/solde/quota :'
    'l''algorithme n''utilise que le volume par forfait et par heure, SurePay reste la source de vérité pour le billing.';

COMMENT ON COLUMN consommation_forfait.forfait_id IS
    'NULLABLE : une consommation peut être débitée directement du solde principal plutôt que d''un forfait (typeForfait vide côté source dans ce cas).'
    'Ces lignes sont conservées pour la traçabilité mais exclues à l''agrégation vers trafic_horaire (WHERE forfait_id IS NOT NULL) - l''algorithme'
    'ne décompose le trafic que par forfait (§4.2 du document technique).';

COMMENT ON COLUMN consommation_forfait.cdr_type IS
    'Seul ''voiceCall'' est confirmé à ce jour. La consommation data se déclenche apparemment sur plusieurs CDR par événement (jusqu''à ~25 pour une'
    'seule session) - probablement avec un ou plusieurs cdrType différents pas encore vus. CHECK à ajouter une fois les valeurs data confirmées.';

COMMENT ON COLUMN consommation_forfait.est_mock IS
    'TRUE si généré manuellement en attendant confirmation des codes forfait réels (847/892 absents du catalogue YAS reçu - voir README §7).'
    'Jamais confondu avec un échantillon SurePay réel, même convention que congestion_horaire.est_mock.';

-- ============================================================================
-- FAMILLE 3 - Agrégat courant (hypertable)
-- ============================================================================

CREATE TABLE observation_horaire (
    bucket        TIMESTAMPTZ NOT NULL,                                                 -- début de l'heure (partition key)
    congestion    FLOAT       NOT NULL    CHECK (congestion >= 0 AND congestion <= 1),  -- agrégé CS/PS/EPS (fraction 0-1)
    qos           FLOAT,
    half_rate     FLOAT,
    alpha_json    JSONB,                                                                -- {"forfait_id": alpha, ...}
    PRIMARY KEY (bucket)
);
SELECT create_hypertable('observation_horaire', by_range('bucket', INTERVAL '7 days'));

COMMENT ON COLUMN observation_horaire.congestion IS
    'Congestion agrégée sur l''heure, moyenne des 3 domaines (CS/PS/EPS), congestion_horaire est déjà réseau '
    'entier (pas de cellule), donc pas de détail supplémentaire à joindre au-delà du domaine lui-même.';

COMMENT ON COLUMN observation_horaire.qos IS
    'QoS horaire agrégée depuis enregistrement_cdr : somme directe du vecteur d''indicateurs QoS individuels par CDR de la période.'
    'QoS reste un indicateur unique (pas décomposé par forfait, contrairement au trafic/alpha_json). Règle définie.';

COMMENT ON TABLE observation_horaire IS
    'jour/heure NE sont PAS stockées : calculer à la lecture via bucket::date / EXTRACT(hour FROM bucket). '
    'trafic_total NON stocké (recalculable = SUM(volume) depuis trafic_horaire pour le bucket), même logique coût que trafic_horaire.trafic_total';

-- ============================================================================
-- FAMILLE 4 - Profils statistiques (hypertables ; recalculés chaque jour)
-- ============================================================================
-- bucket_ref = jour de référence à 00:00 (grain jour). Hypertable pour rester cohérent et bénéficier du chunking, même si le volume est faible.

CREATE TABLE profil_trafic_snapshot (
    bucket_ref      TIMESTAMPTZ NOT NULL,                                   -- jour de référence (00:00), partition key
    forfait_id      VARCHAR(30) NOT NULL REFERENCES forfait(id),
    heure           SMALLINT    NOT NULL CHECK (heure BETWEEN 0 AND 23),    -- heure-du-jour du profil (0-23)
    d_val           FLOAT       NOT NULL,                                   -- moyenne V^k_j (matrice D)
    sigma_val       FLOAT,                                                  -- NULL si un seul jour d'echantillon dans la fenetre (meme cas que profil_qos_snapshot.qos_sigma)
    PRIMARY KEY (bucket_ref, forfait_id, heure)
);
SELECT create_hypertable('profil_trafic_snapshot', by_range('bucket_ref', INTERVAL '30 days'));

COMMENT ON COLUMN profil_trafic_snapshot.heure IS
    'Heure-du-jour du profil (0-23), PAS dérivée de bucket_ref : un snapshot journalier contient '
    'les 24 profils horaires. Colonne réelle, partie de la PK.';

COMMENT ON TABLE profil_trafic_snapshot IS
    'date_reference NON stockée : calculer à la lecture via bucket_ref::date.';

CREATE TABLE profil_qos_snapshot (
    bucket_ref      TIMESTAMPTZ NOT NULL,
    heure           SMALLINT    NOT NULL CHECK (heure BETWEEN 0 AND 23),
    c_moy           FLOAT,
    c_sigma         FLOAT,
    qos_moy         FLOAT,
    qos_sigma       FLOAT,
    hr_moy          FLOAT,
    hr_sigma        FLOAT,
    PRIMARY KEY (bucket_ref, heure)
);
SELECT create_hypertable('profil_qos_snapshot', by_range('bucket_ref', INTERVAL '30 days'));

COMMENT ON COLUMN profil_qos_snapshot.c_moy IS
    'Moyenne de la congestion agrégée (tous domaines CS/PS/EPS confondus, cf. observation_horaire.congestion).';

COMMENT ON TABLE profil_qos_snapshot IS
    'date_reference NON stockée : calculer à la lecture via bucket_ref::date.';

-- ============================================================================
-- FAMILLE 5 - Résultats de l'algorithme (hypertable)
-- ============================================================================
-- Fusion anomalie + alerte_qos + correlation. Le type discrimine ; colonnes spécifiques nullables selon le type.
--   ANOMALIE    : forfait_id, niveau, score (=deviation_score)
--   ALERTE_QOS  : indicateur, score (=valeur_observee), seuil
--   CORRELATION : forfait_id, indicateur, score (=score_pearson), est_causal
CREATE TABLE evenement_detecte (
    id              BIGSERIAL,
    bucket          TIMESTAMPTZ NOT NULL,                                                           -- heure de l'événement (partition key)
    type_evenement  VARCHAR(12) NOT NULL    CHECK (type_evenement IN ('ANOMALIE', 'ALERTE_QOS', 'CORRELATION')),
    forfait_id      VARCHAR(30)             REFERENCES forfait(id),                                 -- ANOMALIE/CORRELATION ; NULL sinon
    indicateur      VARCHAR(10)             CHECK (indicateur IN ('C', 'QoS', 'HR')),               -- ALERTE_QOS/CORRELATION ; NULL sinon
    score           FLOAT       NOT NULL,                                                           -- deviation_score / valeur_observee / score_pearson
    seuil           FLOAT,                                                                          -- ALERTE_QOS ; NULL sinon
    niveau          SMALLINT                CHECK (niveau IN (1, 2)),                               -- ANOMALIE (1=2σ, 2=3σ) ; NULL sinon
    est_causal      BOOLEAN     DEFAULT FALSE,                                                      -- CORRELATION ; FALSE sinon
    -- PK à surrogate : forfait_id/indicateur sont nullables selon le type, une colonne de PK ne peut pas être NULL.
    PRIMARY KEY (bucket, id)
);
SELECT create_hypertable('evenement_detecte', by_range('bucket', INTERVAL '7 days'));

-- Unicité métier : un seul événement par (bucket, type, forfait, indicateur). COALESCE neutralise les NULL.
CREATE UNIQUE INDEX idx_evt_unique ON evenement_detecte (
    bucket, type_evenement,
    COALESCE(forfait_id, ''), COALESCE(indicateur, '')
);
CREATE INDEX idx_evt_type    ON evenement_detecte (type_evenement, bucket DESC);
CREATE INDEX idx_evt_forfait ON evenement_detecte (forfait_id, bucket DESC);

COMMENT ON TABLE evenement_detecte IS
    'Fusion anomalie + alerte_qos + correlation. jour/heure NON stockées : calculer via bucket::date / '
    'EXTRACT(hour FROM bucket).';

-- Vue derivee : nombre d'occurrences par forfait et par jour, pour reperer les forfaits occasionnels/inconnus.
-- Pas de table dediee (recalculable directement depuis consommation_forfait, meme logique que trafic_total non stocke).
CREATE VIEW occurrence_forfait_jour AS
SELECT bucket::date AS jour, forfait_id, count(*) AS occurrences
FROM consommation_forfait
GROUP BY bucket::date, forfait_id;

-- ============================================================================
-- Table de journalisation (normale, PAS une hypertable)
-- ============================================================================

CREATE TABLE rapport (
    id            SERIAL      PRIMARY KEY,
    date_rapport  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    operateur_id  VARCHAR(20) NOT NULL REFERENCES operateur(id),
    chemin_excel  TEXT,
    chemin_pdf    TEXT
);
-- Pas une hypertable : quelques rapports/jour, pas une série temporelle.

-- ============================================================================
-- CHUNK INTERVALS - rappel & réglage
-- ============================================================================
-- Réglés ci-dessus via by_range(col, INTERVAL ...) :
--   enregistrement_cdr, consommation_forfait : 1 jour  (fort volume - à réduire à '1 hour' si l'ingestion dépasse ~des dizaines de M lignes/jour ;
--                                              consommation_forfait peut être verbeuse côté data, jusqu'à ~25 lignes/session)
--   trafic/congestion/obs  : 7 jours (petit volume, borné par forfaits/cellules)
--   profils                : 30 jours (snapshots journaliers, très petit)
-- Le chunk interval est INDÉPENDANT de la fenêtre d'analyse (qui, elle, peut couvrir jours ou mois via un simple WHERE bucket BETWEEN ...).
-- Pour retuner à chaud sur les nouveaux chunks :
--   SELECT set_chunk_time_interval('enregistrement_cdr', INTERVAL '1 hour');
-- Règle : le(s) chunk(s) récent(s) + index doivent tenir en mémoire (~25% RAM).

-- ============================================================================
-- (Optionnel, à activer plus tard) COMPRESSION & RÉTENTION
-- ============================================================================
-- Colonnstore (compression) sur les vieux CDR - 90%+ de gain typique :
--   ALTER TABLE enregistrement_cdr SET (
--       timescaledb.enable_columnstore = true,
--       timescaledb.segmentby = 'type_cdr',
--       timescaledb.orderby   = 'cdr_timestamp DESC'
--   );
--   CALL add_columnstore_policy('enregistrement_cdr', after => INTERVAL '7 days');
-- Rétention (si un jour on veut purger le brut au-delà de N mois) :
--   SELECT add_retention_policy('enregistrement_cdr', INTERVAL '12 months');
--   SELECT add_retention_policy('consommation_forfait', INTERVAL '12 months');
-- À décider (obligations légales de conservation ARCEP) avant activation.

-- =================================================================================
-- Points encore ouverts (voir COMMENT ON) - à lever avant production :
--   1. consommation_forfait.cdr_type  : seul 'voiceCall' confirmé ; valeur(s) côté data à obtenir
--   2. forfait / consommation_forfait : catalogue YAS seedé (392 lignes) ; les 2 échantillons réels de consommation (codes 847, 892) ne matchent aucun code du catalogue - en attente.
--                                       consommation_forfait.est_mock ajoutée pour permettre de continuer avec des données factices en attendant, sans les confondre avec du réel.
--   3. Traffic Class (octet 6, TS 24.008) : différé - transfer_delay_*/traffic_handling_priority_score/signalling_indication décodés sans filtre de validité en attendant
--   4. compression / rétention        : à activer selon obligations de conservation
--   5. congestion_horaire             : structure prête (réseau entier, typée CS/PS/EPS, échelle 0-1) ; aucun échantillon QUALCOP réel reçu à ce jour, non testée sur données réelles
-- Résolus depuis la version précédente (schema_timescaledb v6) :
--   - QoS enregistrement_cdr           : modèle EPC (qos_qci/qos_arp/qos_pci/qos_pvi, qci_caracteristique) retiré - jamais réellement porté par les CDR. Remplacé par les champs TS 24.008 réels (delay_class_score, precedence_class_score, max_bitrate_ul/dl_kbps, residual_ber_score, sdu_error_ratio_score, transfer_delay_ms/score, traffic_handling_priority_score, signalling_indication), décodés depuis qos_negotiated_raw (data uniquement - voix via half_rate/hr_ratio)
--   - observation_horaire.qos          : règle d'agrégation définie (somme directe du vecteur d'indicateurs QoS par CDR) - QoS reste un indicateur unique, pas décomposé par forfait
--   - trafic_horaire.volume_data       : DL uniquement (DL ~90-92% du total) - scission UL/DL abandonnée
--   - congestion_horaire                : réseau entier, pas de cellule_id (le flux QUALCOP fournit un JSON par heure avec 3 valeurs, pas de découpage par cellule);
--                                         échelle valeur passée de % (0-100) à fraction (0-1); ajout de est_mock pour remplacer le marquage par préfixe cellule_id ;
--                                         PK simplifiée en (bucket, type_congestion)
--   - observation_horaire.trafic_total : colonne retirée (recalculable via SUM depuis trafic_horaire pour le bucket) - même logique de coût que trafic_horaire.trafic_total
-- =================================================================================

