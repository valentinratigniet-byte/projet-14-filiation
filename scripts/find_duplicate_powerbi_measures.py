"""Compare plusieurs exports Power BI (--from-json, un par modèle .pbix,
même schéma que extract_powerbi.py) pour repérer les mesures dupliquées
entre rapports — même nom, ou même formule DAX (texte normalisé) sous un
nom différent — signe d'une logique recopiée plutôt que centralisée dans un
modèle partagé.

Contrairement à extract_powerbi.py, ce script n'ajoute AUCUN nouveau nœud :
il ANNOTE les nœuds `dax-measure` déjà présents dans index.html (un par
modèle, déjà mergés via extract_powerbi.py — lancer ce script sur chaque
modèle d'abord) avec un contrôle qualité "Mesure dupliquée entre rapports".
Réutilise le mécanisme quality/pastille/bouton IA "Expliquer l'impact" déjà
en place — aucun code HTML/JS nouveau nécessaire pour l'afficher.

Usage :
    python scripts/extract_powerbi.py --from-json projet09.json
    python scripts/extract_powerbi.py --from-json projet13.json
    python scripts/find_duplicate_powerbi_measures.py --from-json projet09.json --from-json projet13.json --apply

Sans --apply : affiche seulement le rapport (dry-run), n'écrit rien.
Idempotent : ne duplique pas une annotation déjà posée par un run précédent.
"""

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from extract_filiation import SNAPSHOTS_DIR, build_snapshots_block, save_snapshot, splice, to_js_const
from extract_powerbi import slugify
from scan_database import load_existing_real_nodes

DEFAULT_HTML = Path(__file__).resolve().parent.parent / "index.html"
QUALITY_LABEL = "Mesure dupliquée entre rapports"


def normalize_expr(expression: str) -> str:
    return re.sub(r"\s+", " ", expression or "").strip().lower()


def find_duplicates(dumps: list[dict]) -> list[dict]:
    """Chaque entrée : {"kind": "nom"|"formule", "occurrences": [{"model", "measure"}, ...]}."""
    by_name: dict[str, list[dict]] = {}
    by_expr: dict[str, list[dict]] = {}
    for dump in dumps:
        model = dump.get("model", "modèle sans nom")
        for m in dump.get("measures", []):
            occ = {"model": model, "measure": m["name"]}
            by_name.setdefault(m["name"].lower(), []).append(occ)
            expr = normalize_expr(m.get("expression", ""))
            if expr:
                by_expr.setdefault(expr, []).append(occ)

    duplicates = []
    for _, occs in by_name.items():
        models = {o["model"] for o in occs}
        if len(models) > 1:
            duplicates.append({"kind": "nom", "occurrences": occs})
    for _, occs in by_expr.items():
        models = {o["model"] for o in occs}
        if len(models) > 1:
            duplicates.append({"kind": "formule", "occurrences": occs})
    return duplicates


def annotate_nodes(existing: dict[str, Any], duplicates: list[dict]) -> tuple[dict[str, Any], int]:
    updated = dict(existing)
    n_added = 0
    for dup in duplicates:
        for occ in dup["occurrences"]:
            nid = f"dax_measure_{slugify(occ['model'])}_{slugify(occ['measure'])}"
            node = updated.get(nid)
            if not node:
                continue
            others = [o for o in dup["occurrences"] if o is not occ]
            note = f"Même {dup['kind']} que " + ", ".join(f"\"{o['measure']}\" ({o['model']})" for o in others) + "."
            # ponytail: dédup exacte sur le texte de la note — avec 3+ modèles comparés au fil du
            # temps, un groupe qui grossit change le texte (liste plus d'"autres") et une nouvelle
            # entrée s'ajoute plutôt que de remplacer l'ancienne. Sans effet avec 2 modèles (le cas
            # réel aujourd'hui) ; regrouper par (nid, kind) au lieu du texte exact si ça arrive.
            existing_notes = {q.get("note") for q in node.get("quality", [])}
            if note in existing_notes:
                continue
            node.setdefault("quality", []).append({"label": QUALITY_LABEL, "status": "warn", "note": note})
            n_added += 1
    return updated, n_added


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from-json", type=Path, action="append", required=True, dest="dumps", help="Export JSON d'un modèle Power BI — répéter pour comparer plusieurs modèles (au moins 2)")
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML, help="index.html à mettre à jour (avec --apply)")
    parser.add_argument("--snapshots", type=Path, default=SNAPSHOTS_DIR, help="Dossier des instantanés historisés")
    parser.add_argument("--apply", action="store_true", help="Écrit les annotations dans index.html (sans, affiche juste le rapport)")
    args = parser.parse_args()

    if len(args.dumps) < 2:
        raise SystemExit("Fournir au moins deux --from-json pour comparer.")

    dumps = [json.loads(p.read_text(encoding="utf-8")) for p in args.dumps]
    duplicates = find_duplicates(dumps)

    if not duplicates:
        print("Aucune mesure dupliquée trouvée (ni même nom, ni même formule) entre les modèles fournis.")
        return

    print(f"{len(duplicates)} duplication(s) trouvée(s) :")
    for dup in duplicates:
        occ_txt = ", ".join(f"\"{o['measure']}\" ({o['model']})" for o in dup["occurrences"])
        print(f"  - même {dup['kind']} : {occ_txt}")

    if not args.apply:
        print("\n(dry-run — relancer avec --apply pour annoter index.html)")
        return

    existing = load_existing_real_nodes(args.html)
    updated, n_added = annotate_nodes(existing, duplicates)
    if n_added == 0:
        print("Rien à écrire — annotations déjà posées par un run précédent.")
        return

    generated_at = datetime.now(timezone.utc).isoformat()
    splice(args.html, "AUTO-GENERATED", to_js_const("realNodes", updated) + "\n" + to_js_const("REAL_GENERATED_AT", generated_at))
    is_new = save_snapshot(updated, generated_at, args.snapshots, "Détection de mesures dupliquées Power BI")
    splice(args.html, "SNAPSHOTS", build_snapshots_block(args.snapshots))
    print(f"\nOK — {n_added} annotation(s) ajoutée(s), {args.html} mis à jour.")
    print(f"instantané {'ajouté' if is_new else 'déjà présent'} pour {generated_at}")


if __name__ == "__main__":
    main()
