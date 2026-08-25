"""Self-check for scan_database.py's chantier-2 introspection (comments,
views, nullable declared vs observed, defaults, index, CHECK) against a
throwaway SQLite DB — no server, no shared container, no new dependency.

Run: python scripts/test_scan_database.py
"""
import json
import sqlite3
import tempfile
from pathlib import Path

import scan_database as sdb


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE product (
                product_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                price NUMERIC NOT NULL DEFAULT 0,
                category TEXT,
                CHECK (price >= 0)
            );
            CREATE VIEW v_cheap_product AS SELECT * FROM product WHERE price < 10;
            CREATE INDEX idx_product_category ON product(category);
            INSERT INTO product (name, price, category) VALUES ('Widget', 5, 'toys'), ('Gadget', 3, NULL);
            CREATE TABLE centre_cout (
                centre_cout_id INTEGER PRIMARY KEY,
                libelle TEXT,
                responsable TEXT
            );
        """)
        conn.commit()
        conn.close()

        nodes = sdb.scan(f"sqlite:///{db_path}", schemas=["main"])
        by_name = {n["name"]: n for n in nodes.values()}
        product, view = by_name["product"], by_name["v_cheap_product"]
        cols = {c["name"]: c for c in product["columns"]}

        assert product.get("comment") is None, "table comment degrades to None on SQLite (unsupported)"
        assert view.get("isView") is True and "isView" not in product, "view flagged, table isn't"
        assert cols["name"]["nullable"] is False and cols["category"]["nullable"] is True, "declared nullable reflected per column"
        assert cols["price"].get("default") is not None, "column default captured"

        name_nn = {t["label"]: t for t in cols["name"]["tests"]}["Valeurs non nulles"]
        assert name_nn["status"] == "ok", "declared NOT NULL column with no nulls observed ok"

        cat_nn = {t["label"]: t for t in cols["category"]["tests"]}["Valeurs non nulles"]
        assert cat_nn["status"] == "warn" and "NOT NULL" not in (cat_nn.get("note") or ""), (
            "nullable column with a real NULL warns, without a bogus declared-NOT-NULL mismatch note"
        )

        assert any("price" in c["sql"] for c in product.get("checks", [])), "CHECK constraint captured"
        assert any(
            idx["name"] == "idx_product_category" and idx["columns"] == ["category"]
            for idx in product.get("indexes", [])
        ), "explicit index captured with name + columns"

        # Trouvé en dogfooding le rôle RH (2026-08-25) : scan_database.py ne posait
        # aucun marquage RGPD, contrairement à extract_filiation.py -- toute donnée
        # personnelle fusionnée via --merge (bv-postgres-dbtdev, bv-mysql-crm)
        # restait invisible au rôle RH. Vérifie ici que tag_personal_data() est
        # bien appelé par scan() et distingue une vraie colonne personnelle
        # ("responsable", un nom de personne réel trouvé en pratique) d'un
        # libellé métier générique ("libelle", "name", "category").
        centre_cout = by_name["centre_cout"]
        assert "Donnée personnelle (RGPD)" in centre_cout.get("tags", []), "colonne 'responsable' déclenche le marquage RGPD"
        assert "Donnée personnelle (RGPD)" not in product.get("tags", []), "pas de faux positif sur name/category/price"

    print("OK — scan_database.py chantier-2 introspection verified.")


if __name__ == "__main__":
    main()
