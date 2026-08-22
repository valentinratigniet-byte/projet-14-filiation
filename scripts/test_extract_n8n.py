"""Self-check for extract_n8n.py — synthetic workflow JSON, no bv-n8n needed.

Run: python scripts/test_extract_n8n.py
"""
import json
import tempfile
from pathlib import Path

from extract_n8n import build_nodes, build_workflow_node, parse_table_refs


def write_workflow(dir_path: Path, filename: str, name: str, nodes: list) -> Path:
    path = dir_path / filename
    path.write_text(json.dumps({"name": name, "nodes": nodes, "connections": {}}), encoding="utf-8")
    return path


def main() -> None:
    # --- parse_table_refs: known schema vs table alias ---
    refs = parse_table_refs("SELECT e.periode FROM public_marts.fct_sales e JOIN raw.customer c ON c.id = e.customer_id")
    assert refs == {("public_marts", "fct_sales"), ("raw", "customer")}, f"schema.table refs captured, aliases ignored: {refs}"

    refs = parse_table_refs("SELECT unknown_schema.some_table FROM x")
    assert refs == set(), f"unrecognized schema prefix not captured: {refs}"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # Compliant workflow: only touches public_marts (Gold) -> ok
        compliant = write_workflow(tmp_path, "compliant.json", "Compliant workflow", [
            {"name": "Trigger", "type": "n8n-nodes-base.webhook", "parameters": {"path": "trigger-x"}},
            {"name": "Read gold", "type": "n8n-nodes-base.postgres", "parameters": {"query": "SELECT * FROM public_marts.fct_sales"}},
        ])
        table_ids = {"fct_sales": "tbl_fct_sales"}
        node = build_workflow_node(compliant, table_ids)
        assert node["type"] == "pipeline", "node type is 'pipeline'"
        assert node["deps"] == ["tbl_fct_sales"], f"deps resolved via table name match: {node['deps']}"
        assert node["quality"][0]["status"] == "ok", "gold-only access is not flagged"
        assert "webhook/trigger-x" in node["description"], f"trigger path in description: {node['description']}"
        assert len(node["pipelineSteps"]) == 2, "pipeline steps captured in order"

        # Non-compliant: reaches into raw directly -> warn
        risky = write_workflow(tmp_path, "risky.json", "Risky workflow", [
            {"name": "Trigger", "type": "n8n-nodes-base.webhook", "parameters": {"path": "trigger-y"}},
            {"name": "Read raw", "type": "n8n-nodes-base.postgres", "parameters": {"query": "SELECT * FROM raw.customer"}},
        ])
        node = build_workflow_node(risky, table_ids)
        assert node["quality"][0]["status"] == "warn", "direct raw access flagged (bypasses Gold layer)"
        assert "raw" in node["quality"][0]["note"], f"note names the violating schema: {node['quality'][0]['note']}"

        # No postgres node at all -> no quality check fabricated
        no_db = write_workflow(tmp_path, "no_db.json", "No DB workflow", [
            {"name": "Trigger", "type": "n8n-nodes-base.webhook", "parameters": {"path": "trigger-z"}},
            {"name": "Call API", "type": "n8n-nodes-base.httpRequest", "parameters": {"url": "https://example.com"}},
        ])
        node = build_workflow_node(no_db, table_ids)
        assert node["quality"] == [], "no quality check fabricated when there's no Postgres access to judge"
        assert node["deps"] == [], "no deps when no table is referenced"

        # build_nodes: one node per *.json file, ids slugified from workflow name
        existing = {"tbl_fct_sales": {"name": "fct_sales", "short": "fct_sales"}}
        nodes = build_nodes(tmp_path, existing)
        assert set(nodes.keys()) == {"pipeline_compliant_workflow", "pipeline_risky_workflow", "pipeline_no_db_workflow"}, f"ids built from workflow names: {list(nodes.keys())}"

    print("OK — extract_n8n.py workflow parsing and node building verified.")


if __name__ == "__main__":
    main()
