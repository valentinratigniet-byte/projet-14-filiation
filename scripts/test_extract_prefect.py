"""Self-check for extract_prefect.py's pure logic (freshness/status,
task-name cleanup) — no live Prefect connection needed.

Run: python scripts/test_extract_prefect.py
"""
from datetime import datetime, timedelta, timezone

from extract_prefect import build_flow_node, clean_task_name, slugify


def main() -> None:
    assert clean_task_name("extract_load-5ad") == "extract_load", "auto-generated hash suffix stripped"
    assert clean_task_name("dbt_run-e85") == "dbt_run", "auto-generated hash suffix stripped"
    assert clean_task_name("my-custom-name") == "my-custom-name", "a name that happens to end in letters, not a hex suffix, is left alone"
    assert slugify("elt-ecommerce") == "elt_ecommerce", "slugify for node ids"

    now = datetime.now(timezone.utc)
    steps = [{"name": "extract_load", "type": "Completed"}, {"name": "dbt_run", "type": "Completed"}]

    # Recent, successful run -> ok.
    node = build_flow_node("elt-ecommerce", 3, "dazzling-fox", "Completed", now - timedelta(hours=2), steps, now)
    assert node["type"] == "pipeline" and node["domain"] == "Prefect"
    assert node["quality"][0]["status"] == "ok", f"recent successful run is ok: {node['quality']}"
    assert node["quality"][0]["note"] is None, "no note needed when everything is fine"
    assert "dazzling-fox" in node["description"] and "Completed" in node["description"]

    # Old but successful run -> warn (freshness).
    node = build_flow_node("elt-ecommerce", 3, "old-run", "Completed", now - timedelta(days=11), steps, now)
    assert node["quality"][0]["status"] == "warn", f"stale run (>7 days) flagged warn: {node['quality']}"
    assert "11 jour" in node["quality"][0]["note"], f"note names the age: {node['quality'][0]['note']}"

    # Recent but failed run -> fail (status trumps freshness).
    node = build_flow_node("elt-ecommerce", 3, "crashed-run", "Failed", now - timedelta(hours=1), steps, now)
    assert node["quality"][0]["status"] == "fail", f"failed run flagged fail regardless of freshness: {node['quality']}"

    # No run history at all (start_time=None) -> no crash, no fabricated age.
    node = build_flow_node("elt-ecommerce", 1, "no-start-time", "Completed", None, [], now)
    assert node["quality"][0]["status"] == "ok", "missing start_time doesn't fabricate staleness"
    assert node["pipelineSteps"] == [], "empty steps handled"

    print("OK — extract_prefect.py verified.")


if __name__ == "__main__":
    main()
