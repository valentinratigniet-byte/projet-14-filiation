"""Régénère le jeu de données "Projet réel" de Filiation (index.html) à partir
d'un target/ dbt compilé (manifest.json + catalog.json + run_results.json).

Usage :
    python scripts/extract_filiation.py [--target DBT_TARGET_DIR] [--html INDEX_HTML]

Par défaut, lit le dbt_ecommerce de projet-10-pipeline-elt (portfolio-data) et
met à jour index.html à côté de ce script. Relancer après un `dbt run && dbt
docs generate` pour que la doc suive le schéma réel.
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent
DEFAULT_TARGET = HERE.parent.parent / "projet-10-pipeline-elt" / "dbt_ecommerce" / "target"
DEFAULT_HTML = HERE.parent / "index.html"

TEST_LABEL = {
    "unique": "Unicité",
    "not_null": "Valeurs non nulles",
    "relationships": "Intégrité référentielle",
    "accepted_values": "Valeurs autorisées",
}
STATUS_MAP = {"success": "ok", "pass": "ok", "warn": "warn", "fail": "fail", "error": "fail"}
LAYER = {"staging": "Staging", "marts": "Dimensions & faits"}


def js_str(s):
    return json.dumps(s or "", ensure_ascii=False)


def js_list(items):
    return "[" + ", ".join(js_str(x) for x in items) + "]"


def build_tests_index(manifest, run_results):
    status_by_test = {r["unique_id"]: r["status"] for r in run_results["results"]}
    tests_by_target = {}
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


def columns_js(columns, tests_by_target, uid):
    entries = []
    for cname, cv in sorted(columns.items(), key=lambda kv: kv[1]["index"]):
        tests = tests_by_target.get((uid, cname), [])
        test_js = "[" + ", ".join(
            "{ label: %s, status: %s }" % (js_str(t["label"]), js_str(t["status"])) for t in tests
        ) + "]"
        entries.append('        { name: %s, type: %s, tests: %s }' % (js_str(cname), js_str(cv["type"]), test_js))
    return ",\n".join(entries)


def generate(target_dir: Path) -> str:
    manifest = json.loads((target_dir / "manifest.json").read_text(encoding="utf-8"))
    catalog = json.loads((target_dir / "catalog.json").read_text(encoding="utf-8"))
    run_results = json.loads((target_dir / "run_results.json").read_text(encoding="utf-8"))
    tests_by_target = build_tests_index(manifest, run_results)

    lines = ["  const realNodes = {"]

    for uid, v in manifest["sources"].items():
        sname = v["name"]
        cols = catalog["sources"].get(uid, {}).get("columns", {})
        nid = "src_" + sname
        select_cols = ",\n    ".join(sorted(cols, key=lambda x: cols[x]["index"])) if cols else "*"
        sql = f"select\n    {select_cols}\nfrom {v['schema']}.{v['identifier']}"
        lines.append(f"""    {nid}: {{
      domain: "Sources", type: "raw",
      name: {js_str(v['identifier'])}, short: {js_str(v['identifier'])},
      description: {js_str(v.get('description') or 'Aucune description renseignée dans dbt (source externe).')},
      deps: [],
      source: {{ system: "Postgres — {v['database']}", table: "{v['schema']}.{v['identifier']}" }},
      sql: {js_str(sql)},
      columns: [
{columns_js(cols, tests_by_target, uid)}
      ]
    }},""")

    for uid, v in manifest["nodes"].items():
        if v["resource_type"] != "model":
            continue
        name = v["name"]
        cat_cols = catalog["nodes"].get(uid, {}).get("columns", {})
        deps = [r["name"] for r in v.get("refs", [])] + ["src_" + s[1] for s in v.get("sources", [])]
        layer = LAYER.get(v.get("schema"), v.get("schema"))
        lines.append(f"""    {name}: {{
      domain: {js_str(layer)}, type: "derived",
      name: {js_str(name)}, short: {js_str(name)},
      description: {js_str(v.get('description') or 'Aucune description renseignée dans dbt.')},
      deps: {js_list(deps)},
      materialized: {js_str(v['config'].get('materialized'))},
      relation: {js_str(v['database'] + '.' + v['schema'] + '.' + (v.get('alias') or name))},
      sqlKind: "jinja",
      sql: {js_str(v.get('raw_code', ''))},
      columns: [
{columns_js(cat_cols, tests_by_target, uid)}
      ]
    }},""")

    lines.append("  };")
    lines.append(f'  const REAL_GENERATED_AT = {js_str(manifest["metadata"]["generated_at"])};')
    return "\n".join(lines)


def splice(html_path: Path, block: str) -> None:
    html = html_path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"(// AUTO-GENERATED:BEGIN.*?\n)(.*?)(\n\s*// AUTO-GENERATED:END)",
        re.S,
    )
    if not pattern.search(html):
        raise SystemExit("Marqueurs AUTO-GENERATED introuvables dans " + str(html_path))
    new_html = pattern.sub(lambda m: m.group(1) + block + m.group(3), html)
    html_path.write_text(new_html, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET, help="Dossier target/ dbt compilé")
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML, help="index.html à mettre à jour")
    args = parser.parse_args()

    block = generate(args.target)
    splice(args.html, block)
    print(f"OK — {args.html} régénéré depuis {args.target}")


if __name__ == "__main__":
    main()
