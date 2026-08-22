"""Étend Filiation jusqu'à la couche Power BI : mesures et colonnes calculées
DAX, extraites d'un modèle sémantique Power BI, reliées par lignage textuel
(régulier, pas un vrai parseur DAX) aux tables dbt/base déjà présentes dans
Filiation — complète la chaîne base → dbt → Power BI.

Différence avec extract_filiation.py et scan_database.py : CE SCRIPT NE SE
CONNECTE PAS LUI-MÊME à un modèle Power BI. C'est impossible en Python pur —
seul le MCP `powerbi-modeling` (Tabular Object Model via Analysis Services)
sait parler à un modèle Power BI live, et ce MCP n'est accessible que depuis
une session Claude Code, pas depuis un interpréteur Python autonome.

Workflow réel pour regénérer le bloc Power BI :
  1. Ouvrir le .pbix dans Power BI Desktop.
  2. Dans une session Claude Code (MCP powerbi-modeling installé), demander
     l'extraction : connection_operations ListLocalInstances + Connect, puis
     table_operations/measure_operations/column_operations List + Get pour
     récupérer tables, mesures (avec leur expression DAX) et colonnes (avec
     columnType — "Data" ou "Calculated" — et expression si calculée).
     Écrire le résultat dans un fichier JSON — voir powerbi_export.example.json
     pour le schéma exact attendu par --from-json.
  3. python scripts/extract_powerbi.py --from-json <export>.json [--html INDEX_HTML]

Toujours additif : contrairement à scan_database.py (un scan sans --merge
remplace tout), ce script fusionne TOUJOURS avec les nœuds réels déjà
présents — un modèle Power BI s'ajoute à une extraction dbt/base existante,
il ne la remplace jamais (les deux viennent de sources indépendantes).
"""

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from extract_filiation import SNAPSHOTS_DIR, build_snapshots_block, save_snapshot, splice, to_js_const
from scan_database import load_existing_real_nodes

DEFAULT_HTML = Path(__file__).resolve().parent.parent / "index.html"

CONTEXT_TRANSITION_FUNCS = (
    "CALCULATE", "CALCULATETABLE", "FILTER", "ALL", "ALLEXCEPT", "ALLSELECTED", "REMOVEFILTERS",
)

# `Table[Colonne]` (référence à une colonne source) et `[Nom]` sans préfixe
# (référence à une autre mesure du modèle) — regex simple, pas un vrai
# parseur DAX, suffisant pour la majorité des formules réelles.
DAX_TABLE_COL_RE = re.compile(r"([A-Za-z_]\w*)\[([^\]]+)\]")
DAX_BARE_RE = re.compile(r"(?<![\w'\]])\[([^\]]+)\]")


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def parse_dax_refs(expression: str) -> tuple[set[str], list[tuple[str, str]], set[str]]:
    """Renvoie (tables référencées, [(table, colonne), ...], mesures référencées)."""
    column_refs = [(m.group(1), m.group(2)) for m in DAX_TABLE_COL_RE.finditer(expression)]
    tables = {t for t, _ in column_refs}
    # Un `[Nom]` bare qui coïncide avec une colonne déjà capturée par le motif
    # Table[Colonne] n'est pas une référence de mesure (ex. le "[status]" dans
    # `fct_sales[status]` n'est jamais matché ici car précédé d'un identifiant).
    measure_refs = {m.group(1) for m in DAX_BARE_RE.finditer(expression)}
    return tables, column_refs, measure_refs


def has_context_transition(expression: str) -> bool:
    return any(re.search(rf"\b{fn}\s*\(", expression, re.IGNORECASE) for fn in CONTEXT_TRANSITION_FUNCS)


def build_measure_node(m: dict, model_label: str, measure_id_by_name: dict[str, str], table_id_by_name: dict[str, str]) -> dict:
    expression = m.get("expression") or ""
    tables, column_refs, measure_refs = parse_dax_refs(expression)
    measure_refs.discard(m["name"])

    deps = set()
    for t in tables:
        target = table_id_by_name.get(t.lower())
        if target:
            deps.add(target)
    for ref in measure_refs:
        target = measure_id_by_name.get(ref.lower())
        if target:
            deps.add(target)

    multi_table = len({t.lower() for t in tables}) > 1
    risky = multi_table and not has_context_transition(expression)
    quality = [{
        "label": "Contexte de filtre maîtrisé",
        "status": "warn" if risky else "ok",
        "note": (
            f"Référence {len(tables)} tables ({', '.join(sorted(tables))}) sans CALCULATE/FILTER/ALL "
            "explicite — contexte de filtre potentiellement mal maîtrisé, à vérifier."
        ) if risky else None,
    }]

    return {
        "domain": m.get("displayFolder") or "Power BI",
        "type": "dax-measure",
        "name": m["name"],
        "short": m["name"],
        "description": m.get("description") or "Mesure DAX sans description dans le modèle Power BI.",
        "deps": sorted(deps),
        "source": {"system": model_label, "table": m.get("tableName") or "_Mesures"},
        "sql": expression,
        "sqlKind": "dax",
        "refresh": "À la requête (jamais stockée) — contexte de filtre, voir la fiche.",
        "quality": quality,
    }


def build_column_node(c: dict, model_label: str, table_id_by_name: dict[str, str], calculated_column_names: set[str]) -> dict:
    expression = c.get("expression") or ""
    tables, column_refs, _ = parse_dax_refs(expression)

    deps = set()
    for t in tables:
        target = table_id_by_name.get(t.lower())
        if target:
            deps.add(target)

    cascades = any(col.lower() in calculated_column_names for _, col in column_refs)
    quality = [{
        "label": "Pas de cascade de colonnes calculées",
        "status": "warn" if cascades else "ok",
        "note": "Référence une autre colonne calculée — risque de cascade au refresh (recalcul en chaîne)." if cascades else None,
    }]

    return {
        "domain": c["tableName"],
        "type": "dax-column",
        "name": f"{c['tableName']}.{c['name']}",
        "short": c["name"],
        "description": c.get("description") or "Colonne calculée DAX sans description dans le modèle Power BI.",
        "deps": sorted(deps),
        "source": {"system": model_label, "table": c["tableName"]},
        "sql": expression,
        "sqlKind": "dax",
        "refresh": "Au refresh du modèle (stockée physiquement) — contexte de ligne, voir la fiche.",
        "quality": quality,
    }


def build_nodes(dump: dict, existing_real_nodes: dict[str, Any]) -> dict[str, Any]:
    model_label = f"Power BI — {dump.get('model', 'modèle sans nom')}"
    model_slug = slugify(dump.get("model", "powerbi"))

    # Table Power BI -> nœud dbt/base déjà présent dans Filiation, par
    # correspondance de nom (le modèle réutilise le même schéma étoile que
    # les marts dbt déjà extraits — pas une garantie de correspondance
    # physique exacte si plusieurs projets partagent des noms de table).
    table_id_by_name = {}
    for nid, n in existing_real_nodes.items():
        table_id_by_name[(n.get("short") or n.get("name") or "").lower()] = nid

    measures = dump.get("measures", [])
    measure_id_by_name = {m["name"].lower(): f"dax_measure_{model_slug}_{slugify(m['name'])}" for m in measures}

    columns = dump.get("columns", [])
    calculated_columns = [c for c in columns if c.get("columnType") == "Calculated"]
    calculated_column_names = {c["name"].lower() for c in calculated_columns}

    nodes: dict[str, Any] = {}
    for m in measures:
        nodes[measure_id_by_name[m["name"].lower()]] = build_measure_node(m, model_label, measure_id_by_name, table_id_by_name)
    for c in calculated_columns:
        cid = f"dax_column_{model_slug}_{slugify(c['tableName'])}_{slugify(c['name'])}"
        nodes[cid] = build_column_node(c, model_label, table_id_by_name, calculated_column_names)

    return nodes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from-json", type=Path, required=True, help="Export JSON du modèle Power BI (tables/measures/columns) — voir powerbi_export.example.json")
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML, help="index.html à mettre à jour")
    parser.add_argument("--snapshots", type=Path, default=SNAPSHOTS_DIR, help="Dossier des instantanés historisés")
    parser.add_argument("--label", default=None, help="Nom lisible pour cet instantané")
    args = parser.parse_args()

    dump = json.loads(args.from_json.read_text(encoding="utf-8"))
    existing = load_existing_real_nodes(args.html)
    dax_nodes = build_nodes(dump, existing)

    if not dax_nodes:
        raise SystemExit("Aucune mesure ni colonne calculée trouvée dans l'export — rien à ajouter.")

    merged = dict(existing)
    merged.update(dax_nodes)
    generated_at = datetime.now(timezone.utc).isoformat()

    splice(args.html, "AUTO-GENERATED", to_js_const("realNodes", merged) + "\n" + to_js_const("REAL_GENERATED_AT", generated_at))
    is_new = save_snapshot(merged, generated_at, args.snapshots, args.label or f"Power BI — {dump.get('model')}")
    splice(args.html, "SNAPSHOTS", build_snapshots_block(args.snapshots))

    n_measures = sum(1 for n in dax_nodes.values() if n["type"] == "dax-measure")
    n_columns = sum(1 for n in dax_nodes.values() if n["type"] == "dax-column")
    print(f"OK — {n_measures} mesure(s), {n_columns} colonne(s) calculée(s) ajoutée(s), {len(merged)} nœud(s) réels au total, {args.html} mis à jour.")
    print(f"instantané {'ajouté' if is_new else 'déjà présent'} pour {generated_at}")


if __name__ == "__main__":
    main()
