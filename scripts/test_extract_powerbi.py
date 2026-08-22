"""Self-check for extract_powerbi.py's DAX regex parsing and node building —
no live Power BI model needed (that part can only be exercised interactively,
via the MCP, from a Claude Code session with a .pbix open).

Run: python scripts/test_extract_powerbi.py
"""
from extract_powerbi import build_column_node, build_measure_node, build_nodes, has_context_transition, parse_dax_refs


def main() -> None:
    # --- parse_dax_refs: Table[Column] vs bare [Measure] ---
    tables, column_refs, measure_refs = parse_dax_refs("DIVIDE ( [CA], [Nb commandes] )")
    assert tables == set() and column_refs == [], "no Table[Column] refs in a measure-only expression"
    assert measure_refs == {"CA", "Nb commandes"}, f"bare [Name] refs captured as measure refs: {measure_refs}"

    tables, column_refs, measure_refs = parse_dax_refs("CALCULATE ( [CA], fct_sales[status] IN { \"paid\" } )")
    assert tables == {"fct_sales"} and column_refs == [("fct_sales", "status")], "Table[Column] ref captured"
    assert measure_refs == {"CA"}, "bare [CA] still captured alongside a Table[Column] ref in the same expression"

    tables, _, _ = parse_dax_refs("SUM ( fct_sales[line_amount] ) + SUM ( dim_product[price] )")
    assert tables == {"fct_sales", "dim_product"}, f"multiple distinct tables captured: {tables}"

    # --- has_context_transition ---
    assert has_context_transition("CALCULATE ( [CA], ALL ( dim_product[category] ) )") is True
    assert has_context_transition("SUM ( fct_sales[line_amount] )") is False

    # --- build_measure_node: deps + risk heuristic ---
    table_ids = {"fct_sales": "tbl_fct_sales", "dim_product": "tbl_dim_product"}
    measure_ids = {"ca": "dax_measure_ca", "risky sum": "dax_measure_risky_sum"}

    ca = build_measure_node(
        {"name": "CA", "tableName": "_Mesures", "expression": "SUM ( fct_sales[line_amount] )", "displayFolder": "Base"},
        "Power BI — test", measure_ids, table_ids,
    )
    assert ca["deps"] == ["tbl_fct_sales"], f"single-table measure depends on the matched table node: {ca['deps']}"
    assert ca["quality"][0]["status"] == "ok", "single-table measure isn't flagged risky"

    risky = build_measure_node(
        {"name": "Risky sum", "tableName": "_Mesures", "expression": "SUM ( fct_sales[line_amount] ) + SUM ( dim_product[price] )", "displayFolder": "Base"},
        "Power BI — test", measure_ids, table_ids,
    )
    assert set(risky["deps"]) == {"tbl_fct_sales", "tbl_dim_product"}, f"multi-table deps captured: {risky['deps']}"
    assert risky["quality"][0]["status"] == "warn", "multi-table measure without CALCULATE/FILTER/ALL is flagged risky"

    safe_multi = build_measure_node(
        {"name": "Safe multi", "tableName": "_Mesures", "expression": "CALCULATE ( SUM ( fct_sales[line_amount] ), ALL ( dim_product[category] ) )", "displayFolder": "Base"},
        "Power BI — test", measure_ids, table_ids,
    )
    assert safe_multi["quality"][0]["status"] == "ok", "multi-table measure WITH CALCULATE/ALL is not flagged risky"

    panier = build_measure_node(
        {"name": "Panier moyen", "tableName": "_Mesures", "expression": "DIVIDE ( [CA], [Risky sum] )", "displayFolder": "Base"},
        "Power BI — test", measure_ids, table_ids,
    )
    assert set(panier["deps"]) == {"dax_measure_ca", "dax_measure_risky_sum"}, f"measure-to-measure deps resolved: {panier['deps']}"

    # --- build_column_node: cascade detection ---
    calc_names = {"prix_ttc"}
    cascading = build_column_node(
        {"tableName": "dim_product", "name": "marge_pct", "expression": "DIVIDE ( dim_product[prix_ttc] - dim_product[cost], dim_product[prix_ttc] )"},
        "Power BI — test", table_ids, calc_names,
    )
    assert cascading["quality"][0]["status"] == "warn", "calculated column referencing another calculated column is flagged"

    non_cascading = build_column_node(
        {"tableName": "dim_product", "name": "prix_ttc", "expression": "dim_product[price] * 1.2"},
        "Power BI — test", table_ids, calc_names,
    )
    assert non_cascading["quality"][0]["status"] == "ok", "calculated column referencing only data columns is not flagged"

    # --- build_nodes: end-to-end, only Calculated columns become nodes ---
    dump = {
        "model": "Test Model",
        "measures": [{"name": "CA", "tableName": "_Mesures", "expression": "SUM ( fct_sales[line_amount] )"}],
        "columns": [
            {"tableName": "dim_product", "name": "prix_ttc", "columnType": "Calculated", "expression": "dim_product[price] * 1.2"},
            {"tableName": "fct_sales", "name": "line_amount", "columnType": "Data"},
        ],
    }
    existing = {"tbl_fct_sales": {"name": "fct_sales", "short": "fct_sales"}, "tbl_dim_product": {"name": "dim_product", "short": "dim_product"}}
    nodes = build_nodes(dump, existing)
    types = sorted(n["type"] for n in nodes.values())
    assert types == ["dax-column", "dax-measure"], f"exactly one measure and one calculated column node built: {types}"

    print("OK — extract_powerbi.py DAX parsing and node building verified.")


if __name__ == "__main__":
    main()
