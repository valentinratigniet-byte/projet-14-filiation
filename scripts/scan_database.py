"""La "valise de détection" : scanne une base de données quelconque
(Postgres, MySQL, SQLite, SQL Server...) via SQLAlchemy et produit un jeu de
données Filiation — sans dépendre d'un projet dbt. Utile pour auditer un
système inconnu : tables ET vues, colonnes (type, nullable déclaré, valeur
par défaut, commentaire déclaré en base), volumétrie réelle, relations (FK
réelles si déclarées, sinon inférées par convention de nommage comme
extract_filiation.py), index, contraintes CHECK, et des contrôles de qualité
calculés en direct (valeurs non nulles — avec signalement d'incohérence si
une colonne déclarée NOT NULL contient pourtant des valeurs nulles —,
unicité des clés). Chaque introspection non supportée par un dialecte/objet
donné (ex. commentaires sur SQLite, PK/FK sur une vue) dégrade proprement au
lieu de faire planter le scan.

Usage :
    export DATABASE_URL="postgresql://user:pass@host:port/db"   # jamais en argument (historique shell)
    python scripts/scan_database.py [--schemas raw staging] [--html INDEX_HTML] [--label "Nom du système"]

Pour combiner plusieurs systèmes dans le même rapport (ex. un ERP et un CRM),
relancer avec --merge : le scan fusionne avec les nœuds réels déjà présents
au lieu de tout remplacer (comportement par défaut). Les ids de nœuds sont
préfixés par système pour éviter toute collision entre deux tables de même
nom sur deux systèmes différents.

Pour ne pas retaper une URL à chaque audit récurrent, déclarer un alias dans
connections.yml (gitignored, voir connections.example.yml) :
    mon_erp:
      env: MON_ERP_URL
puis lancer avec --conn mon_erp (résout vers $MON_ERP_URL, jamais de secret en
clair dans le fichier). --connect-timeout/--retries bornent une base distante
lente ou temporairement indisponible (sinon le scan pend indéfiniment).

Toujours en lecture seule (SELECT / introspection uniquement). Les
identifiants ne sont jamais écrits dans le HTML ni dans un instantané — seuls
le nom de dialecte et le nom de base apparaissent (ex. "postgresql — ecommerce").
"""

import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError

from extract_filiation import SNAPSHOTS_DIR, build_snapshots_block, infer_fk_guesses, save_snapshot, splice, tag_personal_data, to_js_const

DEFAULT_HTML = Path(__file__).resolve().parent.parent / "index.html"
CONNECTIONS_FILE = Path(__file__).resolve().parent.parent / "connections.yml"

# Clé de connect_args portant le timeout de connexion (secondes), par dialecte —
# chaque pilote DBAPI a sa propre convention de nommage. SQLite est local, pas de
# réseau à borner.
CONNECT_TIMEOUT_ARG = {
    "postgresql": "connect_timeout",
    "mysql": "connect_timeout",
    "mariadb": "connect_timeout",
    "mssql": "timeout",
}

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


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def node_id(system_slug: str, table: str) -> str:
    return f"tbl_{system_slug}_{table}"


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


def quick_row_counts(engine, insp, schemas: list[str]) -> list[tuple[int, str, str]]:
    """COUNT(*) par table, sans introspection de colonnes ni contrôles qualité —
    pour classer les tables avant un scan complet (mode --top)."""
    # ponytail: COUNT(*) sur TOUTES les tables reste un scan complet en soi sur une base
    # à des centaines de tables ; passer aux statistiques du catalogue (pg_class.reltuples
    # sur Postgres, information_schema.tables sur MySQL) si ça devient trop lent en pratique.
    rows = []
    with engine.connect() as conn:
        for schema in schemas:
            for table in insp.get_table_names(schema=schema):
                try:
                    n = conn.execute(text(f"select count(*) from {quoted_table(engine, schema, table)}")).scalar() or 0
                except Exception:
                    n = 0
                rows.append((n, schema, table))
    return rows


def make_engine(url: str, connect_timeout: int | None):
    connect_args = {}
    if connect_timeout:
        dialect = url.split("+", 1)[0].split(":", 1)[0]
        arg = CONNECT_TIMEOUT_ARG.get(dialect)
        if arg:
            connect_args[arg] = connect_timeout
    return create_engine(url, connect_args=connect_args)


def connect_with_retry(engine, retries: int = 3, backoff: float = 1.5):
    """Vérifie la connexion avant de lancer le scan, avec retry + backoff
    exponentiel — une base distante lente/indisponible échouait sinon avec une
    exception SQLAlchemy brute et sans nouvelle tentative."""
    for attempt in range(1, retries + 1):
        try:
            conn = engine.connect()
            conn.close()
            return
        except OperationalError as exc:
            if attempt == retries:
                raise SystemExit(f"Connexion impossible après {retries} tentative(s) : {exc.orig if exc.orig else exc}")
            wait = backoff ** attempt
            print(f"Connexion échouée (tentative {attempt}/{retries}), nouvel essai dans {wait:.1f}s…")
            time.sleep(wait)


def safe(fn, default):
    """Certaines introspections (commentaires, index, contraintes CHECK, PK/FK
    sur une vue) ne sont pas supportées par tous les dialectes/objets — dégrader
    proprement (valeur par défaut) plutôt que planter le scan en entier."""
    try:
        return fn()
    except Exception:
        return default


def scan(url: str, schemas: list[str] | None, tables: list[str] | None = None, connect_timeout: int | None = None, retries: int = 3) -> dict[str, Any]:
    engine = make_engine(url, connect_timeout)
    connect_with_retry(engine, retries=retries)
    insp = inspect(engine)
    schemas = schemas or default_schemas(engine, insp)

    system_label = f"{engine.url.get_backend_name()} — {engine.url.database}"
    system_slug = slugify(system_label)

    id_by_table: dict[tuple[str, str], str] = {}
    is_view: dict[tuple[str, str], bool] = {}
    for schema in schemas:
        for table in insp.get_table_names(schema=schema):
            if tables is not None and table not in tables:
                continue
            id_by_table[(schema, table)] = node_id(system_slug, table)
            is_view[(schema, table)] = False
        # Vues : mêmes id/déps qu'une table, mais get_pk_constraint/get_foreign_keys
        # n'ont pas de sens dessus — dégradées via safe() plus bas plutôt qu'exclues.
        for view in safe(lambda s=schema: insp.get_view_names(schema=s), []):
            if tables is not None and view not in tables:
                continue
            id_by_table[(schema, view)] = node_id(system_slug, view)
            is_view[(schema, view)] = True
    nodes: dict[str, Any] = {}

    with engine.connect() as conn:
        for (schema, table), nid in id_by_table.items():
                view = is_view[(schema, table)]
                cols = insp.get_columns(table, schema=schema)
                pk_cols = set(safe(lambda: (insp.get_pk_constraint(table, schema=schema) or {}).get("constrained_columns") or [], []))
                fks = safe(lambda: insp.get_foreign_keys(table, schema=schema) or [], [])
                table_comment = safe(lambda: (insp.get_table_comment(table, schema=schema) or {}).get("text"), None)
                indexes = [
                    {"name": idx.get("name"), "columns": idx["column_names"], "unique": bool(idx.get("unique"))}
                    for idx in safe(lambda: insp.get_indexes(table, schema=schema) or [], [])
                    if idx.get("column_names")
                ]
                checks = [
                    {"name": c.get("name"), "sql": str(c["sqltext"])}
                    for c in safe(lambda: insp.get_check_constraints(table, schema=schema) or [], [])
                    if c.get("sqltext") is not None
                ]

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
                            # Signal de cohérence : une colonne déclarée NOT NULL dont le
                            # test échoue est une incohérence entre le schéma et les données.
                            if not c.get("nullable", True) and nn["status"] != "ok":
                                nn["note"] = (nn.get("note") or "") + " — pourtant déclarée NOT NULL en base"
                            tests.append(nn)
                        # Unicité testée colonne par colonne uniquement pour une clé simple
                        # (une clé composite n'implique l'unicité d'aucune colonne seule).
                        if pk_cols == {c["name"]}:
                            uq = check_unique(engine, conn, schema, table, c["name"], total)
                            if uq:
                                tests.append(uq)
                    entry = {"name": c["name"], "type": str(c["type"]), "nullable": bool(c.get("nullable", True)), "tests": tests}
                    if c.get("comment"):
                        entry["comment"] = c["comment"]
                    if c.get("default") is not None:
                        entry["default"] = str(c["default"])
                    if c["name"] in fk_by_col:
                        entry["upstream"] = fk_by_col[c["name"]]
                    columns.append(entry)

                relations_txt = "réelles (clés étrangères déclarées)." if fks else "aucune contrainte déclarée ; complétées ci-dessous par une heuristique de nommage."
                if table_comment:
                    description = f"{table_comment} Relations : {relations_txt}"
                else:
                    kind_word = "Vue" if view else "Table"
                    description = f"{kind_word} détectée automatiquement ({schema}.{table}) — aucune documentation associée. Relations : {relations_txt}"

                node = {
                    "domain": schema,
                    "type": "raw",
                    "name": table,
                    "short": table,
                    "description": description,
                    "deps": sorted(set(deps)),
                    "source": {"system": system_label, "table": f"{schema}.{table}"},
                    "queryHint": f"select * from {quoted_table(engine, schema, table)} limit 20;",
                    "columns": columns,
                }
                if view:
                    node["isView"] = True
                if total is not None:
                    node["rowCount"] = total
                if indexes:
                    node["indexes"] = indexes
                if checks:
                    node["checks"] = checks
                nodes[nid] = node

    # Complète, table par table sans contrainte réelle, une heuristique de nommage
    # (xxx_id -> table xxx/xxxs) — utilisée par la vue Systèmes (mini schéma relationnel).
    infer_fk_guesses(nodes)
    tag_personal_data(nodes)
    engine.dispose()
    return nodes


def load_existing_real_nodes(html_path: Path) -> dict[str, Any]:
    """Relit le bloc `realNodes` déjà présent dans index.html — utilisé par
    --merge pour fusionner un nouveau système avec ceux déjà scannés plutôt
    que de tout remplacer. Best-effort : un bloc absent ou illisible donne un
    dict vide (comportement équivalent à un premier scan)."""
    if not html_path.exists():
        return {}
    html = html_path.read_text(encoding="utf-8")
    block_m = re.search(r"// AUTO-GENERATED:BEGIN.*?\n(.*?)\n\s*// AUTO-GENERATED:END", html, re.S)
    if not block_m:
        return {}
    nodes_part = block_m.group(1).split("\n  const REAL_GENERATED_AT")[0]
    try:
        nodes_json = nodes_part.split("const realNodes = ", 1)[1].rstrip().rstrip(";")
        return json.loads(nodes_json)
    except (IndexError, json.JSONDecodeError):
        return {}


def load_connections(path: Path) -> dict[str, Any]:
    """`connections.yml` (gitignored) : alias -> nom de la variable d'env à
    lire, jamais un secret en clair. Absent = pas d'alias disponibles."""
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def resolve_url(args, connections: dict[str, Any]) -> str:
    if args.url:
        return args.url
    if args.conn:
        entry = connections.get(args.conn)
        if not entry:
            raise SystemExit(f"Alias \"{args.conn}\" absent de {CONNECTIONS_FILE} (voir connections.example.yml pour le format).")
        env_var = entry.get("env") if isinstance(entry, dict) else None
        if not env_var:
            raise SystemExit(f"Alias \"{args.conn}\" mal formé dans {CONNECTIONS_FILE} (attendu : {{env: NOM_VARIABLE}}).")
        url = os.environ.get(env_var)
        if not url:
            raise SystemExit(f"Alias \"{args.conn}\" pointe vers ${env_var}, mais cette variable d'environnement n'est pas définie.")
        return url
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("Fournir --url, --conn <alias> (voir connections.yml) ou définir $DATABASE_URL avant de lancer ce script.")
    return url


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=None, help="URL SQLAlchemy — préférer $DATABASE_URL ou --conn pour ne pas exposer le mot de passe dans l'historique shell")
    parser.add_argument("--conn", default=None, help="Alias défini dans connections.yml (résout vers la variable d'environnement associée)")
    parser.add_argument("--schemas", nargs="*", default=None, help="Schémas à scanner (défaut : tous, hors schémas système)")
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML, help="index.html à mettre à jour")
    parser.add_argument("--snapshots", type=Path, default=SNAPSHOTS_DIR, help="Dossier des instantanés historisés")
    parser.add_argument("--label", default=None, help="Nom lisible pour ce système (ex. \"ERP client X\")")
    parser.add_argument("--tables", nargs="*", default=None, help="Limiter le scan à ces tables précises (nom sans schéma)")
    parser.add_argument("--top", type=int, default=None, help="Aperçu rapide : ne scanner en détail que les N tables les plus volumineuses (COUNT(*) sur toutes les tables d'abord)")
    parser.add_argument("--merge", action="store_true", help="Fusionner avec les systèmes déjà scannés (par défaut : un scan remplace tous les nœuds réels précédents)")
    parser.add_argument("--connect-timeout", type=int, default=10, help="Timeout de connexion en secondes (0 pour désactiver, laisse le défaut du pilote)")
    parser.add_argument("--retries", type=int, default=3, help="Nombre de tentatives de connexion avant d'abandonner")
    args = parser.parse_args()

    url = resolve_url(args, load_connections(CONNECTIONS_FILE))

    tables = args.tables
    schemas = args.schemas
    if args.top:
        engine = make_engine(url, args.connect_timeout)
        connect_with_retry(engine, retries=args.retries)
        insp = inspect(engine)
        schemas = schemas or default_schemas(engine, insp)
        ranked = sorted(quick_row_counts(engine, insp, schemas), reverse=True)
        tables = [table for _, _, table in ranked[: args.top]]
        print(f"Aperçu rapide — {args.top} table(s) la/les plus volumineuse(s) retenue(s) sur {len(ranked)} :")
        for n, schema, table in ranked[: args.top]:
            print(f"  {n:>10}  {schema}.{table}")
        engine.dispose()

    nodes = scan(url, schemas, tables, connect_timeout=args.connect_timeout, retries=args.retries)
    scanned_count = len(nodes)
    if args.merge:
        merged = load_existing_real_nodes(args.html)
        merged.update(nodes)
        nodes = merged
    generated_at = datetime.now(timezone.utc).isoformat()

    splice(args.html, "AUTO-GENERATED", to_js_const("realNodes", nodes) + "\n" + to_js_const("REAL_GENERATED_AT", generated_at))
    is_new = save_snapshot(nodes, generated_at, args.snapshots, args.label)
    splice(args.html, "SNAPSHOTS", build_snapshots_block(args.snapshots))

    if args.merge:
        print(f"OK — {scanned_count} table(s) scannée(s) pour ce système, {len(nodes)} au total après fusion, {args.html} mis à jour.")
    else:
        print(f"OK — {len(nodes)} table(s) détectée(s), {args.html} mis à jour.")
    print(f"instantané {'ajouté' if is_new else 'déjà présent'} pour {generated_at}")


if __name__ == "__main__":
    main()
