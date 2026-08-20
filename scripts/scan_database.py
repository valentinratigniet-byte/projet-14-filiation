"""La "valise de détection" : scanne une base de données quelconque
(Postgres, MySQL, SQLite, SQL Server...) via SQLAlchemy et produit un jeu de
données Filiation — sans dépendre d'un projet dbt. Utile pour auditer un
système inconnu : tables, colonnes, volumétrie réelle, relations (FK réelles
si déclarées, sinon inférées par convention de nommage comme
extract_filiation.py), et des contrôles de qualité basiques calculés en
direct (valeurs non nulles, unicité des clés).

Usage :
    export DATABASE_URL="postgresql://user:pass@host:port/db"   # jamais en argument (historique shell)
    python scripts/scan_database.py [--schemas raw staging] [--html INDEX_HTML] [--label "Nom du système"]

Toujours en lecture seule (SELECT / introspection uniquement). Les
identifiants ne sont jamais écrits dans le HTML ni dans un instantané — seuls
le nom de dialecte et le nom de base apparaissent (ex. "postgresql — ecommerce").
"""

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect, text

from extract_filiation import SNAPSHOTS_DIR, build_snapshots_block, infer_fk_guesses, save_snapshot, splice, to_js_const

DEFAULT_HTML = Path(__file__).resolve().parent.parent / "index.html"

# Sur MySQL/MariaDB, "schema" == "database" au sens SQLAlchemy : get_schema_names()
# liste alors TOUTES les bases de l'instance (y compris mysql/performance_schema/sys),
# pas seulement celle ciblée par l'URL de connexion — contrairement à Postgres, où les
# schémas sont des espaces de noms à l'intérieur d'une seule base. Trouvé en testant
# réellement contre une instance MySQL : le scan par défaut remontait 154 tables système
# au lieu des 3 tables réelles.
MYSQL_LIKE_DIALECTS = {"mysql", "mariadb"}


def default_schemas(engine, insp) -> list[str]:
    if engine.dialect.name in MYSQL_LIKE_DIALECTS:
        return [engine.url.database]
    return [s for s in insp.get_schema_names() if s not in ("information_schema", "pg_catalog")]


def node_id(table: str) -> str:
    return "tbl_" + table


def quoted_table(engine, schema: str, table: str) -> str:
    """Identifiant qualifié, quoté selon le dialecte réel (guillemets doubles
    sur Postgres, backticks sur MySQL, crochets sur SQL Server...) — un SQL
    texte codé en dur avec des guillemets doubles casserait silencieusement
    sur MySQL (guillemets doubles = chaîne littérale par défaut, pas un
    identifiant)."""
    prep = engine.dialect.identifier_preparer
    return f"{prep.quote(schema)}.{prep.quote(table)}"


def quoted_col(engine, column: str) -> str:
    return engine.dialect.identifier_preparer.quote(column)


def check_not_null(engine, conn, schema: str, table: str, column: str) -> dict | None:
    try:
        n = conn.execute(text(f"select count(*) from {quoted_table(engine, schema, table)} where {quoted_col(engine, column)} is null")).scalar()
    except Exception:
        return None
    return {"label": "Valeurs non nulles", "status": "ok" if n == 0 else "warn", "note": None if n == 0 else f"{n} valeur(s) nulle(s) détectée(s)"}


def check_unique(engine, conn, schema: str, table: str, column: str, total: int) -> dict | None:
    try:
        n = conn.execute(text(f"select count(distinct {quoted_col(engine, column)}) from {quoted_table(engine, schema, table)}")).scalar()
    except Exception:
        return None
    return {"label": "Unicité", "status": "ok" if n == total else "fail", "note": None if n == total else f"{total - n} doublon(s)"}


def scan(url: str, schemas: list[str] | None) -> dict[str, Any]:
    engine = create_engine(url)
    insp = inspect(engine)
    schemas = schemas or default_schemas(engine, insp)

    id_by_table: dict[tuple[str, str], str] = {}
    for schema in schemas:
        for table in insp.get_table_names(schema=schema):
            id_by_table[(schema, table)] = node_id(table)

    system_label = f"{engine.url.get_backend_name()} — {engine.url.database}"
    nodes: dict[str, Any] = {}

    with engine.connect() as conn:
        for schema in schemas:
            for table in insp.get_table_names(schema=schema):
                nid = id_by_table[(schema, table)]
                cols = insp.get_columns(table, schema=schema)
                pk_cols = set((insp.get_pk_constraint(table, schema=schema) or {}).get("constrained_columns") or [])
                fks = insp.get_foreign_keys(table, schema=schema) or []

                deps, fk_by_col = [], {}
                for fk in fks:
                    ref_schema = fk.get("referred_schema") or schema
                    target = id_by_table.get((ref_schema, fk["referred_table"]))
                    if not target:
                        continue
                    deps.append(target)
                    for local_col, ref_col in zip(fk["constrained_columns"], fk["referred_columns"]):
                        fk_by_col.setdefault(local_col, []).append({"node": target, "column": ref_col})

                try:
                    total = conn.execute(text(f"select count(*) from {quoted_table(engine, schema, table)}")).scalar()
                except Exception:
                    total = None

                columns = []
                for c in cols:
                    tests = []
                    if total is not None:
                        nn = check_not_null(engine, conn, schema, table, c["name"])
                        if nn:
                            tests.append(nn)
                        # Unicité testée colonne par colonne uniquement pour une clé simple
                        # (une clé composite n'implique l'unicité d'aucune colonne seule).
                        if pk_cols == {c["name"]}:
                            uq = check_unique(engine, conn, schema, table, c["name"], total)
                            if uq:
                                tests.append(uq)
                    entry = {"name": c["name"], "type": str(c["type"]), "tests": tests}
                    if c["name"] in fk_by_col:
                        entry["upstream"] = fk_by_col[c["name"]]
                    columns.append(entry)

                node = {
                    "domain": schema,
                    "type": "raw",
                    "name": table,
                    "short": table,
                    "description": f"Table détectée automatiquement ({schema}.{table}) — aucune documentation associée. Relations : {'réelles (clés étrangères déclarées).' if fks else 'aucune contrainte déclarée ; complétées ci-dessous par une heuristique de nommage.'}",
                    "deps": sorted(set(deps)),
                    "source": {"system": system_label, "table": f"{schema}.{table}"},
                    "queryHint": f"select * from {quoted_table(engine, schema, table)} limit 20;",
                    "columns": columns,
                }
                if total is not None:
                    node["rowCount"] = total
                nodes[nid] = node

    # Complète, table par table sans contrainte réelle, une heuristique de nommage
    # (xxx_id -> table xxx/xxxs) — utilisée par la vue Systèmes (mini schéma relationnel).
    infer_fk_guesses(nodes)
    return nodes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=None, help="URL SQLAlchemy — préférer $DATABASE_URL pour ne pas exposer le mot de passe dans l'historique shell")
    parser.add_argument("--schemas", nargs="*", default=None, help="Schémas à scanner (défaut : tous, hors schémas système)")
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML, help="index.html à mettre à jour")
    parser.add_argument("--snapshots", type=Path, default=SNAPSHOTS_DIR, help="Dossier des instantanés historisés")
    parser.add_argument("--label", default=None, help="Nom lisible pour ce système (ex. \"ERP client X\")")
    args = parser.parse_args()

    url = args.url or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("Fournir --url ou définir $DATABASE_URL avant de lancer ce script.")

    nodes = scan(url, args.schemas)
    generated_at = datetime.now(timezone.utc).isoformat()

    splice(args.html, "AUTO-GENERATED", to_js_const("realNodes", nodes) + "\n" + to_js_const("REAL_GENERATED_AT", generated_at))
    is_new = save_snapshot(nodes, generated_at, args.snapshots, args.label)
    splice(args.html, "SNAPSHOTS", build_snapshots_block(args.snapshots))

    print(f"OK — {len(nodes)} table(s) détectée(s), {args.html} mis à jour.")
    print(f"instantané {'ajouté' if is_new else 'déjà présent'} pour {generated_at}")


if __name__ == "__main__":
    main()
