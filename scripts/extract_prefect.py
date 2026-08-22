"""Étend Filiation avec l'orchestration Prefect réelle du portfolio (Projets
04 et 10 font tourner leur pipeline via Prefect — `elt/flow.py`) : un nœud
`type: "pipeline"` par flow, avec le statut et les étapes de sa dernière
exécution, et un contrôle de fraîcheur (dernière exécution récente ou non).

Contrairement à extract_n8n.py (fichiers JSON statiques, n8n reste
inaccessible en API sans authentification), ce script SE CONNECTE en direct
au client Prefect local — Prefect ne demande pas d'authentification pour son
usage local (profil "ephemeral", base SQLite dans ~/.prefect/), donc pas le
même blocage que pour bv-n8n. Nécessite le paquet `prefect`, installé dans
le venv de projet-10-pipeline-elt, pas dans le python système :

Usage :
    ../projet-10-pipeline-elt/.venv/Scripts/python.exe scripts/extract_prefect.py

Toujours additif comme extract_n8n.py/extract_powerbi.py (jamais de
remplacement). Ne modifie rien côté Prefect — lecture seule (read_flows/
read_flow_runs/read_task_runs).
"""

import argparse
import asyncio
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from extract_filiation import SNAPSHOTS_DIR, build_snapshots_block, save_snapshot, splice, to_js_const
from scan_database import load_existing_real_nodes

DEFAULT_HTML = Path(__file__).resolve().parent.parent / "index.html"
STALE_AFTER = timedelta(days=7)


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def clean_task_name(name: str) -> str:
    """Prefect suffixe le nom d'un task run d'un hash court auto-généré
    (`extract_load-5ad`) sauf nom personnalisé — purement cosmétique, sans
    intérêt pour la lecture humaine, retiré s'il correspond au motif."""
    return re.sub(r"-[0-9a-f]{2,4}$", "", name)


def build_flow_node(flow_name: str, run_count: int, latest_run_name: str, latest_state: str,
                     latest_start_time, steps: list[dict], now) -> dict[str, Any]:
    """Pure — pas de dépendance Prefect, testable sans connexion live."""
    age = now - latest_start_time if latest_start_time else None
    stale = age is not None and age > STALE_AFTER
    quality = [{
        "label": "Dernière exécution",
        "status": "fail" if latest_state != "Completed" else ("warn" if stale else "ok"),
        "note": (
            f"Statut {latest_state}" + (f", il y a {age.days} jour(s) — plus de 7 jours" if stale else "")
        ) if (latest_state != "Completed" or stale) else None,
    }]

    step_summary = " → ".join(f"{s['name']} ({s['type']})" for s in steps) or "(aucune étape enregistrée)"
    description = (
        f"Flow Prefect \"{flow_name}\" — {run_count} exécution(s) enregistrée(s) localement. "
        f"Dernière : {latest_run_name}, {latest_state}, "
        f"{latest_start_time.strftime('%Y-%m-%d %H:%M') if latest_start_time else '?'} UTC. "
        f"Étapes : {step_summary}."
    )

    return {
        "domain": "Prefect",
        "type": "pipeline",
        "name": flow_name,
        "short": flow_name,
        "description": description,
        "deps": [],
        "source": {"system": "Prefect — local", "table": flow_name},
        "pipelineSteps": steps,
        "quality": quality,
    }


async def fetch_flow_nodes() -> dict[str, Any]:
    # Import différé : `prefect` n'est installé que dans le venv de
    # projet-10-pipeline-elt, pas dans le python système qui lance les
    # autres scripts de ce dossier.
    from prefect.client.orchestration import get_client
    from prefect.client.schemas.filters import TaskRunFilter, TaskRunFilterFlowRunId

    nodes: dict[str, Any] = {}
    now = datetime.now(timezone.utc)

    async with get_client() as client:
        flows = await client.read_flows()
        all_runs = await client.read_flow_runs(limit=200)
        runs_by_flow: dict[str, list] = {}
        for r in all_runs:
            runs_by_flow.setdefault(str(r.flow_id), []).append(r)

        for f in flows:
            runs = sorted(runs_by_flow.get(str(f.id), []), key=lambda r: r.start_time or now, reverse=True)
            if not runs:
                continue
            latest = runs[0]

            task_runs = await client.read_task_runs(
                task_run_filter=TaskRunFilter(flow_run_id=TaskRunFilterFlowRunId(any_=[latest.id]))
            )
            steps = [
                {"name": clean_task_name(t.name), "type": t.state_name}
                for t in sorted(task_runs, key=lambda t: t.start_time or now)
            ]

            nodes[f"prefect_{slugify(f.name)}"] = build_flow_node(
                f.name, len(runs), latest.name, latest.state_name, latest.start_time, steps, now
            )

    return nodes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML, help="index.html à mettre à jour")
    parser.add_argument("--snapshots", type=Path, default=SNAPSHOTS_DIR, help="Dossier des instantanés historisés")
    parser.add_argument("--label", default=None, help="Nom lisible pour cet instantané")
    args = parser.parse_args()

    pipeline_nodes = asyncio.run(fetch_flow_nodes())
    if not pipeline_nodes:
        raise SystemExit("Aucun flow Prefect avec au moins une exécution trouvée — rien à ajouter.")

    existing = load_existing_real_nodes(args.html)
    merged = dict(existing)
    merged.update(pipeline_nodes)
    generated_at = datetime.now(timezone.utc).isoformat()

    splice(args.html, "AUTO-GENERATED", to_js_const("realNodes", merged) + "\n" + to_js_const("REAL_GENERATED_AT", generated_at))
    is_new = save_snapshot(merged, generated_at, args.snapshots, args.label or "Prefect — local")
    splice(args.html, "SNAPSHOTS", build_snapshots_block(args.snapshots))

    print(f"OK — {len(pipeline_nodes)} flow(s) Prefect ajouté(s), {len(merged)} nœud(s) réels au total, {args.html} mis à jour.")
    print(f"instantané {'ajouté' if is_new else 'déjà présent'} pour {generated_at}")


if __name__ == "__main__":
    main()
