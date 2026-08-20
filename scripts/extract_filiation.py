"""Régénère le jeu de données "Projet réel" de Filiation (index.html) à partir
d'un target/ dbt compilé (manifest.json + catalog.json + run_results.json), et
tient un historique d'extractions pour la détection de dérive.

Usage :
    python scripts/extract_filiation.py [--target DBT_TARGET_DIR] [--html INDEX_HTML]

Par défaut, lit le dbt_ecommerce de projet-10-pipeline-elt (portfolio-data) et
met à jour index.html à côté de ce script. Relancer après un `dbt run && dbt
docs generate` : le schéma courant est mis à jour, ET un instantané horodaté
est ajouté dans snapshots/ (dédupliqué sur le generated_at du manifest) — la
vue "Dérive" de l'outil compare deux instantanés au choix.
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent
DEFAULT_TARGET = HERE.parent.parent / "projet-10-pipeline-elt" / "dbt_ecommerce" / "target"
DEFAULT_HTML = HERE.parent / "index.html"
SNAPSHOTS_DIR = HERE.parent / "snapshots"

TEST_LABEL = {
    "unique": "Unicité",
    "not_null": "Valeurs non nulles",
    "relationships": "Intégrité référentielle",
    "accepted_values": "Valeurs autorisées",
}
STATUS_MAP = {"success": "ok", "pass": "ok", "warn": "warn", "fail": "fail", "error": "fail"}
LAYER = {"staging": "Staging", "marts": "Dimensions & faits"}


def build_tests_index(manifest: dict, run_results: dict) -> dict[tuple[str, str | None], list[dict]]:
    status_by_test = {r["unique_id"]: r["status"] for r in run_results["results"]}
    tests_by_target: dict[tuple[str, str | None], list[dict]] = {}
    for k, v in manifest["nodes"].items():
        if v["resource_type"] != "test":
            continue
        dep_nodes = v.get("depends_on", {}).get("nodes", [])
        col = v.get("column_name")
        tname = v.get("test_metadata", {}).get("name", "other")
        status = STATUS_MAP.get(status_by_test.get(k, ""), "ok")
        label = TEST_LABEL.get(tname, tname)
        if tname == "accepted_values":
            vals = v.get("test_metadata", {}).get("kwargs", {}).get("values", [])
            label = "Valeurs autorisées : " + ", ".join(str(x) for x in vals)
        for target in dep_nodes:
            tests_by_target.setdefault((target, col), []).append({"label": label, "status": status})
    return tests_by_target


def columns_data(columns: dict, tests_by_target: dict, uid: str) -> list[dict]:
    return [
        {"name": cname, "type": cv["type"], "tests": tests_by_target.get((uid, cname), [])}
        for cname, cv in sorted(columns.items(), key=lambda kv: kv[1]["index"])
    ]


def extract_nodes(target_dir: Path) -> tuple[dict[str, Any], str]:
    """Retourne (nœuds, generated_at) à partir d'un target/ dbt compilé."""
    manifest = json.loads((target_dir / "manifest.json").read_text(encoding="utf-8"))
    catalog = json.loads((target_dir / "catalog.json").read_text(encoding="utf-8"))
    run_results = json.loads((target_dir / "run_results.json").read_text(encoding="utf-8"))
    tests_by_target = build_tests_index(manifest, run_results)

    nodes: dict[str, Any] = {}

    for uid, v in manifest["sources"].items():
        cols = catalog["sources"].get(uid, {}).get("columns", {})
        nid = "src_" + v["name"]
        select_cols = ", ".join(sorted(cols, key=lambda x: cols[x]["index"])) if cols else "*"
        nodes[nid] = {
            "domain": "Sources",
            "type": "raw",
            "name": v["identifier"],
            "short": v["identifier"],
            "description": v.get("description") or "Aucune description renseignée dans dbt (source externe).",
            "deps": [],
            "source": {"system": f"Postgres — {v['database']}", "table": f"{v['schema']}.{v['identifier']}"},
            "sql": f"select\n    {select_cols}\nfrom {v['schema']}.{v['identifier']}",
            "columns": columns_data(cols, tests_by_target, uid),
        }

    for uid, v in manifest["nodes"].items():
        if v["resource_type"] != "model":
            continue
        name = v["name"]
        cat_cols = catalog["nodes"].get(uid, {}).get("columns", {})
        deps = [r["name"] for r in v.get("refs", [])] + ["src_" + s[1] for s in v.get("sources", [])]
        nodes[name] = {
            "domain": LAYER.get(v.get("schema"), v.get("schema")),
            "type": "derived",
            "name": name,
            "short": name,
            "description": v.get("description") or "Aucune description renseignée dans dbt.",
            "deps": deps,
            "materialized": v["config"].get("materialized"),
            "relation": v["database"] + "." + v["schema"] + "." + (v.get("alias") or name),
            "sqlKind": "jinja",
            "sql": v.get("raw_code", ""),
            "columns": columns_data(cat_cols, tests_by_target, uid),
        }

    return nodes, manifest["metadata"]["generated_at"]


def to_js_const(var_name: str, data: Any) -> str:
    return f"  const {var_name} = {json.dumps(data, ensure_ascii=False, indent=2)};"


def splice(html_path: Path, marker: str, block: str) -> None:
    html = html_path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"(// {marker}:BEGIN.*?\n)(.*?)(\n\s*// {marker}:END)",
        re.S,
    )
    if not pattern.search(html):
        raise SystemExit(f"Marqueurs {marker} introuvables dans {html_path}")
    new_html = pattern.sub(lambda m: m.group(1) + block + m.group(3), html)
    html_path.write_text(new_html, encoding="utf-8")


def save_snapshot(nodes: dict, generated_at: str, snapshots_dir: Path, label: str | None = None) -> bool:
    """Écrit un instantané JSON, sauf si un instantané existe déjà pour ce generated_at.
    Retourne True si un nouveau fichier a été écrit."""
    snapshots_dir.mkdir(exist_ok=True)
    safe_name = generated_at.replace(":", "-")
    path = snapshots_dir / f"{safe_name}.json"
    if path.exists():
        return False
    path.write_text(
        json.dumps({"label": label or generated_at, "generated_at": generated_at, "nodes": nodes}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return True


def build_snapshots_block(snapshots_dir: Path) -> str:
    snapshots = {}
    if snapshots_dir.exists():
        for f in sorted(snapshots_dir.glob("*.json")):
            snap = json.loads(f.read_text(encoding="utf-8"))
            snapshots[f.stem] = snap
    return to_js_const("SNAPSHOTS", snapshots)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET, help="Dossier target/ dbt compilé")
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML, help="index.html à mettre à jour")
    parser.add_argument("--snapshots", type=Path, default=SNAPSHOTS_DIR, help="Dossier des instantanés historisés")
    parser.add_argument("--label", default=None, help="Nom lisible pour l'instantané de cette extraction")
    args = parser.parse_args()

    nodes, generated_at = extract_nodes(args.target)
    splice(args.html, "AUTO-GENERATED", to_js_const("realNodes", nodes) + "\n" + to_js_const("REAL_GENERATED_AT", generated_at))

    is_new = save_snapshot(nodes, generated_at, args.snapshots, args.label)
    splice(args.html, "SNAPSHOTS", build_snapshots_block(args.snapshots))

    print(f"OK — {args.html} régénéré depuis {args.target}")
    print(f"instantané {'ajouté' if is_new else 'déjà présent'} pour {generated_at}")


if __name__ == "__main__":
    main()
