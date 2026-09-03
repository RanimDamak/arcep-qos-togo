# DEV_SETUP - TimescaleDB en local (Docker) pour le projet ARCEP

But : avoir sur ton PC un PostgreSQL 18 + TimescaleDB **identique à la prod**, sans toucher à ton PostgreSQL 16.9 existant, et y charger `schema.sql`.

Pourquoi Docker plutôt qu'installer TimescaleDB sur ta machine : ça évite de polluer le système, ça reproduit exactement la version de prod (pas de dérive 16 vs 18), et ça se supprime en une commande quand tu as fini. Ton PostgreSQL 16.9 local reste intact et n'est pas utilisé par le projet.

---

## 0. Prérequis (une seule chose à installer)

Installer **Docker Desktop** (Windows). C'est le seul logiciel à ajouter.
Vérifier :

```bash
docker --version
```

---

## 1. Lancer le conteneur TimescaleDB (PostgreSQL 18)

On mappe le port **6543** côté PC (le conteneur écoute sur 5432 en interne).
Ça évite tout conflit avec ton PostgreSQL 16.9 qui occupe déjà 5432.

```bash
docker run -d --name arcep-tsdb ^
  -p 6543:5432 ^
  -e POSTGRES_PASSWORD=arcep_dev ^
  -e POSTGRES_DB=arcep ^
  timescale/timescaledb-ha:pg18
```

> Sur Windows, `^` = continuation de ligne (cmd). En PowerShell, utilise `` ` ``
> (backtick), ou mets tout sur une seule ligne. Une seule ligne, version sûre :
> `docker run -d --name arcep-tsdb -p 6543:5432 -e POSTGRES_PASSWORD=arcep_dev -e POSTGRES_DB=arcep timescale/timescaledb-ha:pg18`

Attendre ~1-2 min le premier démarrage (téléchargement + init). Vérifier :

```bash
docker logs arcep-tsdb           # doit finir par "database system is ready to accept connections"
docker ps                        # arcep-tsdb doit être "Up"
```

> Note : l'image `timescale/timescaledb-ha:pg18` est l'image officielle de dev.
> La doc précise qu'elle est destinée au **développement/test**, pas à la prod -
> ce qui est exactement notre cas ici. La prod utilise l'install "propre" de
> l'équipe.

---

## 2. Vérifier que TimescaleDB est bien chargé

Ouvrir un shell psql DANS le conteneur :

```bash
docker exec -it arcep-tsdb psql -U postgres -d arcep
```

Puis, dans psql :

```sql
SELECT extname, extversion FROM pg_extension WHERE extname = 'timescaledb';
```

- Si une ligne s'affiche → l'extension est déjà activée, parfait.
- Si 0 ligne → l'activer une fois :

```sql
CREATE EXTENSION IF NOT EXISTS timescaledb;
```

Vérifier aussi la version de PostgreSQL (doit être 18) :

```sql
SELECT version();
```

Quitter psql : `\q`

---

## 3. Charger le schéma

Copier le fichier dans le conteneur puis l'exécuter (depuis la racine du projet, qui contient `schema.sql`) :

```bash
docker cp schema.sql arcep-tsdb:/tmp/schema.sql
docker exec -it arcep-tsdb psql -U postgres -d arcep -f /tmp/schema.sql
```

Le script affiche une ligne par `create_hypertable(...)` (le nom de la table convertie) et les INSERT de seed. Pas d'erreur = schéma en place.

Vérifier les hypertables créées :

```bash
docker exec -it arcep-tsdb psql -U postgres -d arcep -c ^
  "SELECT hypertable_name FROM timescaledb_information.hypertables ORDER BY 1;"
```

On dois voir 8 hypertables : `congestion_horaire, consommation_forfait, enregistrement_cdr, evenement_detecte, observation_horaire, profil_qos_snapshot, profil_trafic_snapshot, trafic_horaire`.

Et 3 tables normales (non-hypertables) : `operateur, forfait, rapport` - soit **11 tables** au total, plus la vue `occurrence_forfait_jour`.

---

## 4. Se connecter depuis le code / un client

Paramètres de connexion (dev) :

| Champ     | Valeur       |
|-----------|--------------|
| host      | localhost    |
| port      | **6543**     |
| database  | arcep        |
| user      | postgres     |
| password  | arcep_dev    |

Exemple DSN psycopg (Python) :

```
postgresql://postgres:arcep_dev@localhost:6543/arcep
```

En pratique, la config se fait via `.env` à la racine du projet (voir `.env.example`), lu par `src/donnees/db.py` - pas besoin de modifier le code pour changer ces valeurs.

> DuckDB : rien à installer côté schéma. DuckDB lit les DataFrames chargés
> depuis TimescaleDB (via le connecteur postgres/pandas) ; il n'a pas de tables
> propres. La connexion ci-dessus suffit pour alimenter DuckDB.

---

## 5. Cycle de vie du conteneur (pratique au quotidien)

```bash
docker stop arcep-tsdb     # éteindre (les données restent)
docker start arcep-tsdb    # rallumer
docker rm -f arcep-tsdb    # supprimer le conteneur (⚠ perd les données si pas de volume)
```

> Les données vivent dans le conteneur. Tant que tu ne fais pas `rm`, elles persistent entre `stop`/`start`. Pour un stockage durable indépendant du
> conteneur, on ajoutera un volume Docker (`-v arcep_pgdata:/var/lib/postgresql/data`) - utile plus tard, pas indispensable pour démarrer.

---

## Note sur pg_dump / psql en local (Windows)

Les outils `pg_dump`/`psql` installés localement (liés à ton PostgreSQL 16.9) sont plus anciens que la version 18 du conteneur - Postgres refuse un dump si l'outil client est plus vieux que le serveur (`aborting because of server version mismatch`). Pour dump/reload, exécuter `pg_dump`/`psql` **depuis le conteneur**, pas depuis l'installation locale :

```bash
docker exec -e PGPASSWORD=arcep_dev arcep-tsdb pg_dump -U postgres -d arcep -F c -f /tmp/backup.dump
docker cp arcep-tsdb:/tmp/backup.dump .\backup.dump
```
