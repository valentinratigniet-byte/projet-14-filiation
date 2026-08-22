"""Self-check for find_duplicate_powerbi_measures.py — synthetic dumps, no
live Power BI model needed.

Run: python scripts/test_find_duplicate_powerbi_measures.py
"""
from find_duplicate_powerbi_measures import annotate_nodes, find_duplicates, normalize_expr


def main() -> None:
    assert normalize_expr("SUM ( fct_sales[line_amount] )") == "sum ( fct_sales[line_amount] )", "normalize collapses case/whitespace"
    assert normalize_expr("SUM (  fct_sales[line_amount]  )") == normalize_expr("SUM ( fct_sales[line_amount] )"), "extra whitespace doesn't affect equality"

    dump_a = {"model": "Model A", "measures": [
        {"name": "CA", "expression": "SUM ( fct_sales[line_amount] )"},
        {"name": "Only in A", "expression": "COUNTROWS ( fct_sales )"},
    ]}
    dump_b = {"model": "Model B", "measures": [
        {"name": "CA", "expression": "SUM (  fct_sales[line_amount]  )"},  # same formula, extra whitespace
        {"name": "Renamed CA", "expression": "SUM ( fct_sales[line_amount] )"},  # different name, same formula as A's CA
    ]}

    dups = find_duplicates([dump_a, dump_b])
    kinds = {(d["kind"], tuple(sorted(o["measure"] for o in d["occurrences"]))) for d in dups}
    assert ("nom", ("CA", "CA")) in kinds, f"same-name duplicate across models found: {kinds}"
    # All three measures share the same normalized formula (A's CA, B's CA — extra whitespace
    # only —, and B's "Renamed CA") -> one formula-duplicate group of 3, name and formula
    # duplication are independent checks (a rename doesn't hide a copy-pasted formula).
    formula_group = next((d for d in dups if d["kind"] == "formule"), None)
    assert formula_group is not None, f"a same-formula duplicate group exists: {kinds}"
    assert {o["measure"] for o in formula_group["occurrences"]} == {"CA", "Renamed CA"}, (
        f"formula group includes both the same-named and the renamed measure: {formula_group}"
    )
    assert not any("Only in A" in (o["measure"] for o in d["occurrences"]) for d in dups), "a measure unique to one model is never flagged"

    # --- annotate_nodes: idempotent, only touches nodes that exist ---
    existing = {
        "dax_measure_model_a_ca": {"name": "CA", "quality": [{"label": "x", "status": "ok", "note": None}]},
        "dax_measure_model_b_ca": {"name": "CA", "quality": []},
        "dax_measure_model_b_renamed_ca": {"name": "Renamed CA", "quality": []},
    }
    updated, n_added = annotate_nodes(existing, dups)
    assert n_added > 0, "at least one annotation added"
    assert len(updated["dax_measure_model_a_ca"]["quality"]) == 3, (
        f"original quality entry preserved, 2 duplicate annotations appended (name + formula): {updated['dax_measure_model_a_ca']['quality']}"
    )

    # Re-running with the already-annotated nodes adds nothing (idempotent).
    _, n_added_again = annotate_nodes(updated, dups)
    assert n_added_again == 0, f"re-running on already-annotated nodes adds 0 (idempotent): {n_added_again}"

    # A dump referencing a model/measure with no matching node in `existing` is silently skipped, not an error.
    dump_c = {"model": "Model C (never extracted)", "measures": [{"name": "CA", "expression": "SUM ( fct_sales[line_amount] )"}]}
    dups_with_c = find_duplicates([dump_a, dump_c])
    updated_c, _ = annotate_nodes({"dax_measure_model_a_ca": {"name": "CA", "quality": []}}, dups_with_c)
    assert "quality" in updated_c["dax_measure_model_a_ca"], "no crash when a duplicate's counterpart node doesn't exist in index.html yet"

    print("OK — find_duplicate_powerbi_measures.py verified.")


if __name__ == "__main__":
    main()
