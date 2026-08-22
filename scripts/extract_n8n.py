"""Étend Filiation jusqu'à l'orchestration réelle : un nœud `type: "pipeline"`
par workflow n8n versionné (JSON exporté), avec ses étapes et un lignage
textuel best-effort vers les tables dbt/base déjà présentes dans Filiation.

Contrairement à extract_powerbi.py, ce script est autonome : les workflows
n8n sont déjà versionnés en JSON sur disque (`n8n/workflows/*.json`), aucune
connexion live à une instance n8n n'est nécessaire — pas d'API, pas
d'authentification, lecture de fichiers uniquement.

Par défaut, lit les workflows du projet partagé `projet-baptiste-valentin`
(sibling de `portfolio-data/` — voir [[projet-baptiste-valentin]] dans la
mémoire du projet) : ce projet fait tourner le hub n8n réel (`bv-n8n`) dont
Filiation illustre l'orchestration. Override possible via --workflows-dir
pour n'importe quel autre dossier de workflows n8n exportés.

Usage :
    python scripts/extract_n8n.py [--workflows-dir DIR] [--html INDEX_HTML]

Toujours additif comme extract_powerbi.py (jamais de remplacement).
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
DEFAULT_WORKFLOWS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "projet-baptiste-valentin" / "n8n" / "workflows"

# Schémas Postgres connus dans le projet partagé (convention dbt de ce
# projet, pas une donnée générique) — sert à repérer un `schema.table` dans
# le texte d'une requête sans confondre avec un alias de table (`e.periode`).
KNOWN_SCHEMAS = {"public_marts", "raw", "erp_migre", "marts", "staging"}
GOLD_SCHEMAS = {"public_marts", "marts"}

TABLE_REF_RE = re.compile(r"\b([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\b")

NODE_TYPE_SHORT = {
    "n8n-nodes-base.webhook": "déclencheur",
    "n8n-nodes-base.postgres": "postgres",
    "n8n-nodes-base.s3": "stockage (S3/MinIO)",
    "n8n-nodes-base.httpRequest": "appel HTTP",
    "n8n-nodes-base.set": "transformation",
    "n8n-nodes-base.code": "code",
    "n8n-nodes-base.noOp": "point d'intégration (placeholder)",
    "n8n-nodes-base.spreadsheetFile": "génération de fichier",
}


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def short_type(n8n_type: str) -> str:
    return NODE_TYPE_SHORT.get(n8n_type, n8n_type.replace("n8n-nodes-base.", ""))


def parse_table_refs(text: str) -> set[tuple[str, str]]:
    return {(schema, table) for schema, table in TABLE_REF_RE.findall(text) if schema in KNOWN_SCHEMAS}


def build_workflow_node(path: Path, table_id_by_name: dict[str, str]) -> dict[str, Any]:
    wf = json.loads(path.read_text(encoding="utf-8"))
    nodes = wf.get("nodes", [])

    trigger = next((n for n in nodes if n.get("type") == "n8n-nodes-base.webhook"), None)
    trigger_path = (trigger.get("parameters") or {}).get("path") if trigger else None

    postgres_queries = [
        (n.get("parameters") or {}).get("query", "")
        for n in nodes if n.get("type") == "n8n-nodes-base.postgres"
    ]
    all_query_text = "\n\n".join(q for q in postgres_queries if q)
    table_refs = parse_table_refs(all_query_text)
    schemas_referenced = {schema for schema, _ in table_refs}

    deps = set()
    for _, table in table_refs:
        target = table_id_by_name.get(table.lower())
        if target:
            deps.add(target)

    steps = [{"name": n.get("name", "?"), "type": short_type(n.get("type", "?"))} for n in nodes]
    step_summary = " → ".join(s["type"] for s in steps)
    trigger_txt = f"déclenché par POST /webhook/{trigger_path}" if trigger_path else "sans déclencheur webhook identifié"
    description = f"Workflow n8n {trigger_txt}. Étapes : {step_summary}."

    quality = []
    if postgres_queries:
        violating = schemas_referenced - GOLD_SCHEMAS
        quality.append({
            "label": "Accès Postgres limité à la couche Gold",
            "status": "warn" if violating else "ok",
            "note": (
                f"Référence directement {', '.join(sorted(violating))} — contourne la couche "
                "public_marts (Gold), règle d'or du projet."
            ) if violating else None,
        })

    return {
        "domain": "n8n",
        "type": "pipeline",
        "name": wf.get("name", path.stem),
        "short": wf.get("name", path.stem),
        "description": description,
        "deps": sorted(deps),
        "source": {"system": "n8n — bv-dataplatform", "table": path.name},
        "sql": all_query_text or None,
        "pipelineSteps": steps,
        "quality": quality,
    }


def build_nodes(workflows_dir: Path, existing_real_nodes: dict[str, Any]) -> dict[str, Any]:
    table_id_by_name = {}
    for nid, n in existing_real_nodes.items():
        table_id_by_name[(n.get("short") or n.get("name") or "").lower()] = nid

    nodes: dict[str, Any] = {}
    for path in sorted(workflows_dir.glob("*.json")):
        node = build_workflow_node(path, table_id_by_name)
        nodes[f"pipeline_{slugify(node['name'])}"] = node
    return nodes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workflows-dir", type=Path, default=DEFAULT_WORKFLOWS_DIR, help="Dossier de workflows n8n exportés en JSON")
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML, help="index.html à mettre à jour")
    parser.add_argument("--snapshots", type=Path, default=SNAPSHOTS_DIR, help="Dossier des instantanés historisés")
    parser.add_argument("--label", default=None, help="Nom lisible pour cet instantané")
    args = parser.parse_args()

    if not args.workflows_dir.exists():
        raise SystemExit(f"Dossier introuvable : {args.workflows_dir} (--workflows-dir pour pointer ailleurs)")

    existing = load_existing_real_nodes(args.html)
    pipeline_nodes = build_nodes(args.workflows_dir, existing)

    if not pipeline_nodes:
        raise SystemExit(f"Aucun workflow (*.json) trouvé dans {args.workflows_dir} — rien à ajouter.")

    merged = dict(existing)
    merged.update(pipeline_nodes)
    generated_at = datetime.now(timezone.utc).isoformat()

    splice(args.html, "AUTO-GENERATED", to_js_const("realNodes", merged) + "\n" + to_js_const("REAL_GENERATED_AT", generated_at))
    is_new = save_snapshot(merged, generated_at, args.snapshots, args.label or "n8n — bv-dataplatform")
    splice(args.html, "SNAPSHOTS", build_snapshots_block(args.snapshots))

    print(f"OK — {len(pipeline_nodes)} workflow(s) ajouté(s), {len(merged)} nœud(s) réels au total, {args.html} mis à jour.")
    print(f"instantané {'ajouté' if is_new else 'déjà présent'} pour {generated_at}")


if __name__ == "__main__":
    main()
