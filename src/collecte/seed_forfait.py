import csv
import sys

from src.donnees.db import connexion


def seed_forfait(csv_path, operateur_id):
    with open(csv_path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    data = [(r["code"], r["forfait"], operateur_id) for r in rows]

    with connexion() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO forfait (id, nom, operateur_id) VALUES (%s,%s,%s) "
                "ON CONFLICT (id) DO NOTHING",
                data
            )
    print(f"{len(data)} forfaits insérés/skippés pour {operateur_id}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m src.collecte.seed_forfait <chemin_csv> [operateur_id]")
        sys.exit(1)
    chemin_csv = sys.argv[1]
    operateur_id = sys.argv[2] if len(sys.argv) > 2 else "YAS"
    seed_forfait(chemin_csv, operateur_id)