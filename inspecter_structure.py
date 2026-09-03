"""Inspecte la structure d'un fichier CDR JSON (cles racine, presence de 'templates', premier
enregistrement) sans supposer qu'il suit le meme format que les echantillons Moov MSC deja valides.
Usage : python inspecter_structure.py "C:\\chemin\\vers\\fichier.json"
"""
import json
import sys
from pathlib import Path

chemin = Path(sys.argv[1])

with open(chemin, encoding="utf-8") as f:
    doc = json.load(f)

print(f"=== {chemin.name} ===")
print(f"Type racine : {type(doc).__name__}")

if isinstance(doc, dict):
    print(f"Cles racine : {sorted(doc.keys())}")
    if "templates" in doc and isinstance(doc["templates"], list):
        print(f"'templates' present, {len(doc['templates'])} enregistrement(s)")
        if doc["templates"]:
            premier = doc["templates"][0]
            print("Premier enregistrement :")
            for cle in sorted(premier.keys()):
                print(f"    {cle}: {premier[cle]!r}")
    else:
        print("PAS de cle 'templates' liste - structure differente des echantillons Moov MSC.")
elif isinstance(doc, list):
    print(f"Racine = liste, {len(doc)} element(s)")
    if doc:
        print("Premier element :")
        premier = doc[0]
        if isinstance(premier, dict):
            for cle in sorted(premier.keys()):
                print(f"    {cle}: {premier[cle]!r}")
        else:
            print(f"    {premier!r}")
else:
    print(f"Contenu (apercu) : {str(doc)[:300]!r}")