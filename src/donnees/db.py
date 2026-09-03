"""Connexion à la base TimescaleDB (psycopg 3).

Config lue depuis un fichier .env (via python-dotenv) à la racine du projet, avec repli sur les vraies variables
d'environnement (utile en production, où le mot de passe est injecté par l'environnement et non par un fichier).

Variables attendues (voir .env.example) : ARCEP_DB_HOST, ARCEP_DB_PORT, ARCEP_DB_NAME, ARCEP_DB_USER, ARCEP_DB_PASSWORD
"""
from __future__ import annotations

import contextlib
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

# Charge le .env de la racine du projet s'il existe. En prod (pas de .env), load_dotenv ne fait rien et on lit les variables d'environnement réelles.
_RACINE = Path(__file__).resolve().parents[2]
load_dotenv(_RACINE / ".env")


def _config() -> dict:
    return {
        "host": os.environ.get("ARCEP_DB_HOST", "localhost"),
        "port": int(os.environ.get("ARCEP_DB_PORT", "6543")),
        "dbname": os.environ.get("ARCEP_DB_NAME", "arcep"),
        "user": os.environ.get("ARCEP_DB_USER", "postgres"),
        "password": os.environ.get("ARCEP_DB_PASSWORD", "arcep_dev"),
    }


def chaine_connexion() -> str:
    c = _config()
    return (
        f"host={c['host']} port={c['port']} dbname={c['dbname']} "
        f"user={c['user']} password={c['password']}"
    )


@contextlib.contextmanager
def connexion():
    """Context manager : ouvre une connexion, commit si OK, rollback sinon, referme dans tous les cas.

        with connexion() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
    """
    conn = psycopg.connect(chaine_connexion())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
