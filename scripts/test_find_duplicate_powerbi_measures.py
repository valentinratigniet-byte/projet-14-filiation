"""Self-check for find_duplicate_powerbi_measures.py — synthetic dumps, no
live Power BI model needed.

Run: python scripts/test_find_duplicate_powerbi_measures.py
"""
from find_duplicate_powerbi_measures import (
    LABEL_CONSISTENT,
    LABEL_DIVERGENT,
    annotate_nodes,
    find_duplicates,
    migrate_existing,
    normalize_expr,
)


def main() -> None:
    assert normalize_expr("SUM ( fct_sales[line_amount] )") == "sum ( fct_sales[line_amount] )", "normalize collapses case/whitespace"
    assert normalize_expr("SUM (  fct_sales[line_amount]  )") == normalize_expr("SUM ( fct_sales[line_amount] )"), "extra whitespace doesn't affect equality"
    assert normalize_expr("RANKX ( ALL ( t[c] ), [CA], , DESC, DENSE )") == normalize_expr("RANKX ( ALL ( t[c] ), [CA],, DESC, DENSE )"), (
        "spacing around a comma (incl. an empty positional arg like ', ,' vs ',,') doesn't affect equality -- "
        "real false-negative found on the project's own 'Rang produit' measure"
    )

    # "CA" : même nom, même formule (whitespace mis à part) dans les 2 modèles -> cohérent.
    # "Growth" : même nom, formule DIFFÉRENTE -> divergence réelle.
    # "Only in A" : unique à un modèle, jamais signalé.
    dump_a = {"model": "Model A", "measures": [
        {"name": "CA", "expression": "SUM ( fct_sales[line_amount] )"},
        {"name": "Growth", "expression": "DIVIDE ( [CA] - [CA N-1], [CA N-1] )"},
        {"name": "Only in A", "expression": "COUNTROWS ( fct_sales )"},
    ]}
    dump_b = {"model": "Model B", "measures": [
        {"name": "CA", "expression": "SUM (  fct_sales[line_amount]  )"},
        {"name": "Growth", "expression": "([CA] - [CA N-1]) / [CA]"},
    ]}

    results = find_duplicates([dump_a, dump_b])
    by_nid = {r["nid"]: r for r in results}

    assert by_nid["dax_measure_model_a_ca"]["status"] == "warn", "consistent duplicate (same formula) stays a warning"
    assert by_nid["dax_measure_model_a_ca"]["label"] == LABEL_CONSISTENT
    assert by_nid["dax_measure_model_b_ca"]["status"] == "warn"

    assert by_nid["dax_measure_model_a_growth"]["status"] == "fail", "divergent formula under the same name is escalated to fail"
    assert by_nid["dax_measure_model_a_growth"]["label"] == LABEL_DIVERGENT
    assert "Model B" in by_nid["dax_measure_model_a_growth"]["note"]
    assert by_nid["dax_measure_model_b_growth"]["status"] == "fail"

    assert "dax_measure_model_a_only_in_a" not in by_nid, "a measure unique to one model is never flagged"
    assert len(results) == 4, f"exactly 4 annotations (CA x2 consistent, Growth x2 divergent): {len(results)}"

    # --- annotate_nodes: idempotent, only touches nodes that exist ---
    existing = {
        "dax_measure_model_a_ca": {"name": "CA", "quality": [{"label": "x", "status": "ok", "note": None}]},
        "dax_measure_model_b_ca": {"name": "CA", "quality": []},
        "dax_measure_model_a_growth": {"name": "Growth", "quality": []},
        "dax_measure_model_b_growth": {"name": "Growth", "quality": []},
    }
    updated, n_added = annotate_nodes(existing, results)
    assert n_added == 4, f"one annotation per result: {n_added}"
    assert len(updated["dax_measure_model_a_ca"]["quality"]) == 2, "original quality entry preserved, 1 consolidated annotation appended"
    assert updated["dax_measure_model_a_growth"]["quality"][0]["status"] == "fail"

    _, n_added_again = annotate_nodes(updated, results)
    assert n_added_again == 0, f"re-running on already-annotated nodes adds 0 (idempotent): {n_added_again}"

    # A dump referencing a model/measure with no matching node in `existing` is silently skipped, not an error.
    dump_c = {"model": "Model C (never extracted)", "measures": [{"name": "CA", "expression": "SUM ( fct_sales[line_amount] )"}]}
    results_with_c = find_duplicates([dump_a, dump_c])
    updated_c, _ = annotate_nodes({"dax_measure_model_a_ca": {"name": "CA", "quality": []}}, results_with_c)
    assert "quality" in updated_c["dax_measure_model_a_ca"], "no crash when a duplicate's counterpart node doesn't exist in index.html yet"

    # --- migrate_existing: reconciles the old dual-pill format (one script version ago),
    # recomputing the formula comparison from each node's own `sql` field rather than
    # trusting the old "Même formule que..." pill (which used the pre-fix normalize_expr).
    old_format = {
        "dax_measure_x_ca": {
            "name": "CA", "sqlKind": "dax", "sql": "SUM ( fct_sales[line_amount] )",
            "quality": [
                {"label": "Contexte de filtre maîtrisé", "status": "ok", "note": None},
                {"label": "Mesure dupliquée entre rapports", "status": "warn", "note": 'Même nom que "CA" (Model Y).'},
                {"label": "Mesure dupliquée entre rapports", "status": "warn", "note": 'Même formule que "CA" (Model Y).'},
            ],
        },
        "dax_measure_model_y_ca": {"name": "CA", "sqlKind": "dax", "sql": "SUM ( fct_sales[line_amount] )", "quality": []},
        "dax_measure_x_growth": {
            "name": "Growth", "sqlKind": "dax", "sql": "RANKX ( ALL ( t[c] ), [CA], , DESC, DENSE )",
            "quality": [
                {"label": "Mesure dupliquée entre rapports", "status": "warn", "note": 'Même nom que "Growth" (Model Y).'},
                # pas de pastille "même formule" associée à l'époque -- mais la vraie formule
                # (recalculée ci-dessous) est en fait équivalente, seul l'espacement diffère.
            ],
        },
        "dax_measure_model_y_growth": {"name": "Growth", "sqlKind": "dax", "sql": "RANKX ( ALL ( t[c] ), [CA],, DESC, DENSE )", "quality": []},
        "dax_measure_x_margin": {
            "name": "Margin", "sqlKind": "dax", "sql": "DIVIDE ( [Profit], [CA] )",
            "quality": [{"label": "Mesure dupliquée entre rapports", "status": "warn", "note": 'Même nom que "Margin" (Model Y).'}],
        },
        "dax_measure_model_y_margin": {"name": "Margin", "sqlKind": "dax", "sql": "[Profit] / [CA]", "quality": []},  # vraiment différent
        "dax_measure_x_untouched": {"name": "Untouched", "quality": [{"label": "Contexte de filtre maîtrisé", "status": "ok", "note": None}]},
    }
    migrated, n_changed = migrate_existing(old_format)
    assert n_changed == 3, f"the 3 nodes with old-format pills are touched: {n_changed}"

    ca_quality = migrated["dax_measure_x_ca"]["quality"]
    assert len(ca_quality) == 2, f"the unrelated 'ok' entry survives, the 2 old pills become 1: {ca_quality}"
    dup_entry = next(q for q in ca_quality if q["label"] != "Contexte de filtre maîtrisé")
    assert dup_entry["label"] == LABEL_CONSISTENT and dup_entry["status"] == "warn", f"CA had a matching formula pill -> stays a consistent warning: {dup_entry}"

    growth_quality = migrated["dax_measure_x_growth"]["quality"]
    assert len(growth_quality) == 1
    assert growth_quality[0]["label"] == LABEL_CONSISTENT and growth_quality[0]["status"] == "warn", (
        f"Growth's old pill never recorded a formula match (pre-fix normalize_expr missed it), but recomputing "
        f"from the real `sql` fields with the fixed normalize_expr finds them equivalent -> consistent, not fail: {growth_quality}"
    )

    margin_quality = migrated["dax_measure_x_margin"]["quality"]
    assert len(margin_quality) == 1
    assert margin_quality[0]["label"] == LABEL_DIVERGENT and margin_quality[0]["status"] == "fail", (
        f"Margin's formulas are genuinely different between models -> escalated to fail: {margin_quality}"
    )

    assert migrated["dax_measure_x_untouched"]["quality"] == old_format["dax_measure_x_untouched"]["quality"], "a node with no old-format pill is left untouched"

    # Idempotent: running migrate_existing again on the already-migrated data changes nothing
    # (the new consolidated notes don't match the old "Même nom que"/"Même formule que" prefixes).
    _, n_changed_again = migrate_existing(migrated)
    assert n_changed_again == 0, f"re-running migrate_existing on migrated data is a no-op: {n_changed_again}"

    print("OK — find_duplicate_powerbi_measures.py verified.")


if __name__ == "__main__":
    main()
