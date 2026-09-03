-- =======================================================================
-- Fallback: native partitioning, used only if TimescaleDB is unavailable.
-- =======================================================================

-- =================================================================================================
-- État : intègre toutes les décisions prises à ce jour ; les points encore ouverts sont documentés
-- en COMMENT ON ci-dessous (pas de valeur arbitraire choisie à la place de l'équipe).
-- =================================================================================================

-- Partitionnement natif PostgreSQL (PARTITION BY RANGE), sans extension.
-- TimescaleDB n'est pas installé sur le serveur cible (vérifié : 0 ligne dans pg_available_extensions). Compatible PostgreSQL >= 10. Si TimescaleDB est
-- installé plus tard, ces tables pourront être migrées vers des hypertables sans changer le reste du schéma (mêmes PK, mêmes colonnes).
-- Voir le bloc "PARTITIONS" en fin de fichier pour la création des partitions mensuelles et la fonction d'aide à réutiliser pour les mois suivants.

-- ============================================================================
-- FAMILLE 1 - Données de référence (statiques, écrites une fois)
-- ============================================================================

CREATE TABLE operateur (
    id      VARCHAR(20)  PRIMARY KEY,          -- ex: 'MOOV', 'YAS'
    nom     VARCHAR(100) NOT NULL
);

CREATE TABLE forfait (
    id            VARCHAR(30)  PRIMARY KEY,
    nom           VARCHAR(150) NOT NULL,
    type          VARCHAR(10)  NOT NULL CHECK (type IN ('voix', 'data', 'mixte')),
    operateur_id  VARCHAR(20)  NOT NULL REFERENCES operateur(id)
);

COMMENT ON TABLE forfait IS
    'Pas de colonne de validité (date_debut/date_fin) à ce jour. Confirmé par l''équipe (24/06/2026) : les forfaits occasionnels ont une période '
    'de validité, mais le mécanisme de détection/exposition reste à définir (discussion prévue). Ne pas ajouter de colonne avant cette clarification.';

-- Caractéristiques standardisées de chaque QCI (TS 23.203, table 6.1.7).
-- Table de référence STATIQUE, chargée une fois. Jointe à enregistrement_cdr.qos_qci pour analyser la QoS
-- par priorité, délai (PDB) et taux de perte (PELR) SANS dupliquer ces valeurs sur chaque CDR (~4500/h).
CREATE TABLE qci_caracteristique (
    qci                     SMALLINT    PRIMARY KEY,                                            -- 1-9 standard ; 128-254 si spécifique opérateur
    resource_type           VARCHAR(7)  NOT NULL CHECK (resource_type IN ('GBR', 'non-GBR')),
    priority_level          SMALLINT    NOT NULL,                                               -- 1 = plus haute (TS 23.203 table 6.1.7)
    packet_delay_budget_ms  INTEGER     NOT NULL,                                               -- PDB en ms
    packet_error_loss_rate  VARCHAR(8)  NOT NULL,                                               -- PELR, ex. '1E-2'
    exemple_service         VARCHAR(60)
);

COMMENT ON TABLE qci_caracteristique IS
    'Valeurs fixées par la norme (TS 23.203 table 6.1.7), donc stockées une seule fois ici et jointes sur qos_qci, plutôt que répétées par CDR. '
    'Décodage du QCI : voir doc "Éléments QoS CDR" (octet 6 pour S-CDR, AVP qCI 1028 pour EPC). N''est exploitable '
    'que lorsque le flux CDR porte effectivement le QCI (absent des échantillons reçus à ce jour).';

-- Seed connu à ce jour (à compléter avec la liste réelle des ~20-30 forfaits)
INSERT INTO operateur (id, nom) VALUES
    ('MOOV', 'Moov Africa Togo'),
    ('YAS',  'Togocel (YAS)');

-- Seed des QCI standardisés 1-9 (TS 23.203 table 6.1.7).
INSERT INTO qci_caracteristique
    (qci, resource_type, priority_level, packet_delay_budget_ms, packet_error_loss_rate, exemple_service) VALUES
    (1, 'GBR',     2, 100, '1E-2', 'Voix conversationnelle'),
    (2, 'GBR',     4, 150, '1E-3', 'Vidéo conversationnelle (live)'),
    (3, 'GBR',     3,  50, '1E-3', 'Jeux temps réel'),
    (4, 'GBR',     5, 300, '1E-6', 'Vidéo non-conversationnelle (buffered)'),
    (5, 'non-GBR', 1, 100, '1E-6', 'Signalisation IMS'),
    (6, 'non-GBR', 6, 300, '1E-6', 'Vidéo (buffered), TCP (web, mail, ftp)'),
    (7, 'non-GBR', 7, 100, '1E-3', 'Voix, vidéo (live), jeux interactifs'),
    (8, 'non-GBR', 8, 300, '1E-6', 'Vidéo (buffered), TCP (web, mail, ftp)'),
    (9, 'non-GBR', 9, 300, '1E-6', 'Vidéo (buffered), TCP - défaut abonnés non prioritaires');

-- ============================================================================
-- FAMILLE 2 - Observations brutes (écrites en continu / toutes les heures)
-- ============================================================================

-- Agrégat horaire de trafic par forfait (source : TRAFSCAN)
CREATE TABLE trafic_horaire (
    jour            DATE      NOT NULL,
    heure           SMALLINT  NOT NULL CHECK (heure BETWEEN 0 AND 23),
    forfait_id      VARCHAR(30) NOT NULL REFERENCES forfait(id),
    volume_voix_fr  FLOAT     DEFAULT 0,
    volume_voix_hr  FLOAT     DEFAULT 0,
    volume_data     FLOAT     DEFAULT 0,
    trafic_total    FLOAT     DEFAULT 0,
    PRIMARY KEY (jour, heure, forfait_id)
) PARTITION BY RANGE (jour);
CREATE INDEX idx_trafic_forfait ON trafic_horaire (forfait_id, jour);

COMMENT ON COLUMN trafic_horaire.volume_data IS
    'Colonne unique UL+DL confondus. Ouvert : l''échantillon PGW réel (cdrType="gw") montre uplink/downlink déjà natifs et distincts dans le '
    'pipeline CDR. Scinder en volume_data_ul / volume_data_dl est proposé mais PAS encore appliqué - à valider avant modification (cf. résumé §10.5).';

-- Compteurs de congestion par cellule (source : QUALCOP/OMC)
CREATE TABLE congestion_horaire (
    jour        DATE        NOT NULL,
    heure       SMALLINT    NOT NULL CHECK (heure BETWEEN 0 AND 23),
    cellule_id  VARCHAR(50) NOT NULL,
    valeur      FLOAT       NOT NULL,
    PRIMARY KEY (jour, heure, cellule_id)
) PARTITION BY RANGE (jour);

-- CDR individuels (source : CollecteurCDR - JSON MOOV/YAS ou octet-string S-CDR)
-- NB granularité : une ligne = un CDR réel, pas un agrégat horaire (cf. note en tête de fichier sur la correction de la clé primaire).
CREATE TABLE enregistrement_cdr (
    id                  BIGSERIAL,
    cdr_reference       VARCHAR(50),                                            -- ex: champ "cdrReference" du JSON
    jour                DATE        NOT NULL,
    heure               SMALLINT    NOT NULL CHECK (heure BETWEEN 0 AND 23),
    cdr_timestamp       TIMESTAMP   NOT NULL,                                   -- ex: champ "dateTime" du JSON, précision seconde
    forfait_id          VARCHAR(30) REFERENCES forfait(id),                     -- nullable, cf. comment ci-dessous
    type_cdr            VARCHAR(10) NOT NULL CHECK (type_cdr IN ('S-CDR', 'PGW-CDR', 'SGW-CDR', 'MSC-CDR')),
    uplink              FLOAT,                                                  -- data : volume montant (octets), natif au CDR
    downlink            FLOAT,                                                  -- data : volume descendant (octets), natif au CDR
    qos_negotiated_raw  BYTEA,                                                  -- S-CDR uniquement (octet-string, TS 29.274)
    qos_qci             SMALLINT    REFERENCES qci_caracteristique(qci),        -- TS 23.203 ; PGW/SGW via EPCQoSInformation.qCI (TS 29.212)
    qos_arp             SMALLINT    CHECK (qos_arp BETWEEN 1 AND 15),           -- Priority Level décodé, uniforme S-CDR/EPC
    qos_pci             BOOLEAN,                                                -- Pre-emption Capability (octet 5 / AVP 1047) ; NULL hors EPC
    qos_pvi             BOOLEAN,                                                -- Pre-emption Vulnerability (octet 5 / AVP 1048) ; NULL hors EPC
    qos_spec_version    VARCHAR(10) DEFAULT 'partiel',                          -- 'partiel' = QCI+ARP(PL/PCI/PVI) seulement, pas MBR/GBR en colonnes
    half_rate           BOOLEAN     DEFAULT FALSE,                              -- domaine CS/MSC (voix)
    hr_ratio            FLOAT       DEFAULT 0,
    PRIMARY KEY (jour, id)
) PARTITION BY RANGE (jour);
CREATE INDEX idx_cdr_forfait    ON enregistrement_cdr (forfait_id, jour, heure);
CREATE INDEX idx_cdr_reference  ON enregistrement_cdr (cdr_reference);
CREATE INDEX idx_cdr_type       ON enregistrement_cdr (type_cdr, jour);

COMMENT ON COLUMN enregistrement_cdr.forfait_id IS
    'NULLABLE par nécessité : sur les 3 échantillons réels reçus à ce jour (MOOV voix, YAS voix, MOOV data/gw), '
    'les champs typeForfait/tarifPlan du JSON existent mais sont toujours null. Lien CDR->forfait non résolu ';

COMMENT ON COLUMN enregistrement_cdr.type_cdr IS
    'L''échantillon PGW réel utilise cdrType="gw" générique, sans distinguer PGW-CDR de SGW-CDR. '
    'Mappé ici par défaut sur ''PGW-CDR'' - à confirmer si la distinction est nécessaire.';

COMMENT ON COLUMN enregistrement_cdr.qos_qci IS
    'Toujours NULL sur les échantillons reçus à ce jour : le JSON actuel ne porte aucun champ QoS. Sera peuplé une fois le JSON '
    'enrichi par l''équipe (champs demandés : qci, arpPriorityLevel, mbrUplink/Downlink, gbrUplink/Downlink - cf. résumé §10.4). '
    'FK vers qci_caracteristique pour joindre priorité/PDB/PELR à l''analyse.';

COMMENT ON COLUMN enregistrement_cdr.qos_pci IS
    'Pre-emption Capability décodé de l''octet 5 (ARP). TRUE = ce bearer peut préempter d''autres bearers. S-CDR : bit de l''octet 5 ; EPC : AVP '
    'Pre-emption-Capability (1047, TS 29.212). NULL hors EPC / si non décodé. Stocke le bit brut ; le label enabled/disabled est dérivé à l''analyse.';

COMMENT ON COLUMN enregistrement_cdr.qos_pvi IS
    'Pre-emption Vulnerability décodé de l''octet 5 (ARP). TRUE = ce bearer peut être préempté. S-CDR : bit de l''octet 5 ; EPC : AVP '
    'Pre-emption-Vulnerability (1048, TS 29.212). NULL hors EPC / si non décodé.';

-- ============================================================================
-- FAMILLE 3 - Agrégat courant (calculé à chaque cycle horaire)
-- ============================================================================

CREATE TABLE observation_horaire (
    jour          DATE      NOT NULL,
    heure         SMALLINT  NOT NULL CHECK (heure BETWEEN 0 AND 23),
    trafic_total  FLOAT     NOT NULL,
    congestion    FLOAT,
    qos           FLOAT,
    half_rate     FLOAT,
    alpha_json    JSONB,                                                -- {"forfait_id": alpha, ...}
    PRIMARY KEY (jour, heure)
) PARTITION BY RANGE (jour);

COMMENT ON COLUMN observation_horaire.qos IS
    'QoS horaire agrégée depuis enregistrement_cdr (qos_qci / qos_arp), éventuellement enrichie via la jointure '
    'qci_caracteristique (priorité, PDB, PELR) et les bits qos_pci / qos_pvi - pas seulement le QCI brut. '
    'Règle d''agrégation : À DÉFINIR (ex. part de sessions dégradées sur l''heure [recommandé], pire QCI, QCI moyen, GBR moyen). ';

-- ============================================================================
-- FAMILLE 4 - Profils statistiques (snapshots recalculés chaque jour)
-- ============================================================================

CREATE TABLE profil_trafic_snapshot (
    date_reference  DATE        NOT NULL,
    forfait_id      VARCHAR(30) NOT NULL REFERENCES forfait(id),
    heure           SMALLINT    NOT NULL CHECK (heure BETWEEN 0 AND 23),
    d_val           FLOAT       NOT NULL,                                   -- moyenne V^k_j (matrice D)
    sigma_val       FLOAT       NOT NULL,                                   -- écart-type σ^k_j (matrice σ)
    PRIMARY KEY (date_reference, forfait_id, heure)
) PARTITION BY RANGE (date_reference);

CREATE TABLE profil_qos_snapshot (
    date_reference  DATE      NOT NULL,
    heure           SMALLINT  NOT NULL CHECK (heure BETWEEN 0 AND 23),
    c_moy           FLOAT,
    c_sigma         FLOAT,
    qos_moy         FLOAT,
    qos_sigma       FLOAT,
    hr_moy          FLOAT,
    hr_sigma        FLOAT,
    PRIMARY KEY (date_reference, heure)
) PARTITION BY RANGE (date_reference);

-- ============================================================================
-- FAMILLE 5 - Résultats de l'algorithme
-- ============================================================================

-- Table UNIQUE des événements détectés. Remplace les anciennes anomalie, alerte_qos et correlation : toutes décrivaient
-- « un événement à (jour, heure)avec un score », même clé, même cycle d'écriture, mêmes lecteurs (Rapport).
-- Le champ type_evenement distingue le détecteur d'origine ; les colonnes spécifiques sont nullables et remplies selon le type.
--   ANOMALIE     : forfait_id, niveau, score (= deviation_score)               ; indicateur/seuil/est_causal NULL
--   ALERTE_QOS   : indicateur, score (= valeur_observee), seuil                ; forfait_id/niveau NULL
--   CORRELATION  : forfait_id, indicateur, score (= score_pearson),est_causal  ; niveau/seuil NULL
CREATE TABLE evenement_detecte (
    id              BIGSERIAL,
    jour            DATE        NOT NULL,
    heure           SMALLINT    NOT NULL CHECK (heure BETWEEN 0 AND 23),
    type_evenement  VARCHAR(12) NOT NULL CHECK (type_evenement IN ('ANOMALIE', 'ALERTE_QOS', 'CORRELATION')),
    forfait_id      VARCHAR(30) REFERENCES forfait(id),                                             -- ANOMALIE/CORRELATION ; NULL pour ALERTE_QOS
    indicateur      VARCHAR(10) CHECK (indicateur IN ('C', 'QoS', 'HR')),                           -- ALERTE_QOS/CORRELATION ; NULL pour ANOMALIE
    score           FLOAT       NOT NULL,                                                           -- deviation_score / valeur_observee / score_pearson
    seuil           FLOAT,                                                                          -- ALERTE_QOS ; NULL sinon
    niveau          SMALLINT    CHECK (niveau IN (1, 2)),                                           -- ANOMALIE (1=2σ, 2=3σ) ; NULL sinon
    est_causal      BOOLEAN     DEFAULT FALSE,                                                      -- CORRELATION ; FALSE sinon
    -- PK = (jour, id) : jour est obligatoire pour le partitionnement, id est un surrogate. On NE met PAS forfait_id / indicateur dans la PK car ils sont
    -- nullables selon le type (une colonne de PRIMARY KEY ne peut pas être NULL sous PostgreSQL). L'unicité métier est garantie par l'index ci-dessous.
    PRIMARY KEY (jour, id)
) PARTITION BY RANGE (jour);

-- Unicité métier : un seul événement par (jour, heure, type, forfait, indicateur).
-- COALESCE neutralise le piège « chaque NULL est distinct » des index UNIQUE, de sorte que 2 ALERTE_QOS identiques (forfait_id NULL) ne passent pas 2 fois.
CREATE UNIQUE INDEX idx_evt_unique ON evenement_detecte (
    jour, heure, type_evenement,
    COALESCE(forfait_id, ''), COALESCE(indicateur, '')
);
CREATE INDEX idx_evt_type     ON evenement_detecte (type_evenement, jour, heure);
CREATE INDEX idx_evt_forfait  ON evenement_detecte (forfait_id, jour);

COMMENT ON TABLE evenement_detecte IS
    'Fusion de anomalie + alerte_qos + correlation (optimisation v3). Écrite par DetecteurAnomalie.analyser(), AlerteQoS.verifier() et '
    'AnalyseurCorrelation.calculer() selon type_evenement ; lue par Heatmap et Rapport. Variante stricte possible si l''on veut éviter '
    'les colonnes nullables : fusionner seulement ANOMALIE + CORRELATION (toutes deux par forfait) et garder ALERTE_QOS à part.';

CREATE TABLE rapport (
    id            SERIAL      PRIMARY KEY,
    date_rapport  TIMESTAMP   NOT NULL DEFAULT NOW(),
    operateur_id  VARCHAR(20) NOT NULL REFERENCES operateur(id),
    chemin_excel  TEXT,
    chemin_pdf    TEXT
);
-- Pas une hypertable : quelques rapports/jour max, pas une série temporelle.

-- ============================================================================
-- PARTITIONS - création des partitions mensuelles (partitionnement natif)
-- ============================================================================
-- Fonction d'aide : crée une partition mensuelle pour une table donnée.
-- Réutilisable pour les mois suivants : SELECT creer_partition_mensuelle('trafic_horaire', 2026, 9);
CREATE OR REPLACE FUNCTION creer_partition_mensuelle(
    p_table TEXT, p_annee INT, p_mois INT
) RETURNS void AS $$
DECLARE
    v_partition_name TEXT;
    v_start DATE;
    v_end DATE;
BEGIN
    v_start := make_date(p_annee, p_mois, 1);
    v_end   := v_start + INTERVAL '1 month';
    v_partition_name := p_table || '_' || to_char(v_start, 'YYYY_MM');
    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)',
        v_partition_name, p_table, v_start, v_end
    );
END;
$$ LANGUAGE plpgsql;

-- Partitions couvrant le développement pour les 6 tables partitionnées par `jour`. À étendre mois par mois en rappelant la fonction.
-- (evenement_detecte remplace anomalie + alerte_qos + correlation : 3 -> 1.)
DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'trafic_horaire', 'congestion_horaire', 'enregistrement_cdr', 'observation_horaire', 'evenement_detecte'
    ] LOOP
        PERFORM creer_partition_mensuelle(t, 2026, 6);
        PERFORM creer_partition_mensuelle(t, 2026, 7);
        PERFORM creer_partition_mensuelle(t, 2026, 8);
        PERFORM creer_partition_mensuelle(t, 2026, 9);
    END LOOP;
END $$;

-- Les 2 tables partitionnées par `date_reference` (profils statistiques)
DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['profil_trafic_snapshot', 'profil_qos_snapshot'] LOOP
        PERFORM creer_partition_mensuelle(t, 2026, 6);
        PERFORM creer_partition_mensuelle(t, 2026, 7);
        PERFORM creer_partition_mensuelle(t, 2026, 8);
        PERFORM creer_partition_mensuelle(t, 2026, 9);
    END LOOP;
END $$;

-- Partitions DEFAULT : filet de sécurité pour toute date hors des plages créées ci-dessus (évite un échec d'INSERT si une
-- partition mensuelle a été oubliée). À surveiller : si cette partition grossit, il manque une partition mensuelle à créer.
DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'trafic_horaire', 'congestion_horaire', 'enregistrement_cdr', 'observation_horaire',
        'evenement_detecte', 'profil_trafic_snapshot', 'profil_qos_snapshot'
    ] LOOP
        EXECUTE format('CREATE TABLE IF NOT EXISTS %I PARTITION OF %I DEFAULT', t || '_default', t);
    END LOOP;
END $$;

-- =================================================================================================================================
-- Récapitulatif des points encore ouverts (voir COMMENT ON ci-dessus pour le détail) - à lever avant un déploiement de production :
--   1. enregistrement_cdr.forfait_id  : lien CDR -> forfait non résolu
--   2. observation_horaire.qos        : règle d'agrégation horaire à définir
--   3. trafic_horaire.volume_data     : scission UL/DL proposée, pas appliquée
--   4. enregistrement_cdr.type_cdr    : mapping 'gw' -> PGW vs SGW à confirmer
--   5. forfait                        : pas de colonne de validité (mécanisme de détection encore à définir)
--   6. qci_caracteristique / qos_qci  : exploitables seulement quand le flux CDR portera réellement le QCI
--   7. evenement_detecte              : fusion 3->1 avec colonnes nullables ; variante stricte 3->2 possible
-- =================================================================================================================================