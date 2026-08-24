"""Compare plusieurs exports Power BI (--from-json, un par modèle .pbix,
même schéma que extract_powerbi.py) pour repérer les mesures dupliquées
entre rapports — même nom sous des modèles différents — et distinguer deux
cas très différents pour la gouvernance :
  - même nom ET même formule DAX (texte normalisé) : duplication cohérente,
    à centraliser un jour mais pas un risque immédiat (statut "warn").
  - même nom mais formule DIFFÉRENTE : deux rapports affichent un chiffre
    sous le même nom mais ne le calculent pas pareil — silencieusement
    incohérent, le vrai risque business (statut "fail").

Contrairement à extract_powerbi.py, ce script n'ajoute AUCUN nouveau nœud :
il ANNOTE les nœuds `dax-measure` déjà présents dans index.html (un par
modèle, déjà mergés via extract_powerbi.py — lancer ce script sur chaque
modèle d'abord) avec un contrôle qualité "Mesure dupliquée entre rapports"
(cohérent) ou "Définitions divergentes entre rapports" (fail). Réutilise le
mécanisme quality/pastille/bouton IA "Expliquer l'impact" déjà en place —
aucun code HTML/JS nouveau nécessaire pour l'afficher.

Usage :
    python scripts/extract_powerbi.py --from-json projet09.json
    python scripts/extract_powerbi.py --from-json projet13.json
    python scripts/find_duplicate_powerbi_measures.py --from-json projet09.json --from-json projet13.json --apply

Sans --apply : affiche seulement le rapport (dry-run), n'écrit rien.
Idempotent : ne duplique pas une annotation déjà posée par un run précédent.

--migrate : reconcilie les anciennes annotations (posées par une version
précédente de ce script, qui ajoutait "Même nom que..." et "Même formule
que..." comme deux pastilles séparées, toutes deux en "warn" — impossible
à distinguer visuellement d'une divergence réelle) en pastilles consolidées
selon le nouveau schéma, sans requérir de nouveaux --from-json.
"""

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from extract_filiation import SNAPSHOTS_DIR, build_snapshots_block, save_snapshot, splice, to_js_const
from extract_powerbi import slugify
from scan_database import load_existing_real_nodes

DEFAULT_HTML = Path(__file__).resolve().parent.parent / "index.html"
LABEL_CONSISTENT = "Mesure dupliquée entre rapports"
LABEL_DIVERGENT = "Définitions divergentes entre rapports"
OLD_LABEL = "Mesure dupliquée entre rapports"  # libellé unique utilisé par l'ancien schéma (nom ET formule)


def normalize_expr(expression: str) -> str:
    text = re.sub(r"\s+", " ", expression or "").strip().lower()
    # Un run précédent avait manqué "CA" vs "Rang produit" comme identiques entre
    # modèles à cause du seul espacement autour d'un argument positionnel vide
    # (`, ,` vs `,,` dans un RANKX) -- la collapse d'espaces ci-dessus ne touche
    # pas l'espace COLLÉ à une virgule. Normaliser aussi ça pour comparer la
    # structure DAX, pas sa mise en forme.
    return re.sub(r"\s*,\s*", ",", text)


def find_duplicates(dumps: list[dict]) -> list[dict]:
    """Une entrée par mesure dupliquée : {"nid", "label", "status", "note"}."""
    entries = []
    for dump in dumps:
        model = dump.get("model", "modèle sans nom")
        for m in dump.get("measures", []):
            entries.append({
                "nid": f"dax_measure_{slugify(model)}_{slugify(m['name'])}",
                "model": model,
                "measure": m["name"],
                "expr": normalize_expr(m.get("expression", "")),
            })

    by_name: dict[str, list[dict]] = {}
    for e in entries:
        by_name.setdefault(e["measure"].lower(), []).append(e)

    results = []
    for _, occs in by_name.items():
        if len({o["model"] for o in occs}) < 2:
            continue
        for occ in occs:
            others = [o for o in occs if o is not occ]
            divergent = [o for o in others if o["expr"] != occ["expr"]]
            if divergent:
                note = "Formule différente de " + ", ".join(f"\"{o['measure']}\" ({o['model']})" for o in divergent) + " sous le même nom — vérifier laquelle fait foi."
                results.append({"nid": occ["nid"], "label": LABEL_DIVERGENT, "status": "fail", "note": note})
            else:
                note = "Même nom et même formule que " + ", ".join(f"\"{o['measure']}\" ({o['model']})" for o in others) + " — duplication cohérente, à centraliser si possible."
                results.append({"nid": occ["nid"], "label": LABEL_CONSISTENT, "status": "warn", "note": note})
    return results


def annotate_nodes(existing: dict[str, Any], results: list[dict]) -> tuple[dict[str, Any], int]:
    updated = dict(existing)
    n_added = 0
    for r in results:
        node = updated.get(r["nid"])
        if not node:
            continue
        existing_notes = {q.get("note") for q in node.get("quality", [])}
        if r["note"] in existing_notes:
            continue
        node.setdefault("quality", []).append({"label": r["label"], "status": r["status"], "note": r["note"]})
        n_added += 1
    return updated, n_added


_NOTE_PARTNER_RE = re.compile(r'"([^"]+)"\s*\(([^)]+)\)')


def migrate_existing(existing: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Reconcilie les pastilles posées par l'ancien schéma (une pastille "warn"
    par "Même nom que..." + une pastille "warn" séparée par "Même formule
    que..." quand elle s'applique aussi) en une seule pastille consolidée par
    partenaire : "fail" (LABEL_DIVERGENT) ou "warn" (LABEL_CONSISTENT). Les
    partenaires viennent des anciennes pastilles "Même nom que..." (seule
    trace de qui est dupliqué avec qui, sans redemander à Power BI), mais la
    comparaison de formule est RECALCULÉE depuis le champ `sql` déjà présent
    sur chaque nœud DAX plutôt que relue depuis l'ancienne pastille "Même
    formule que..." — celle-ci datait d'un `normalize_expr` qui ratait une
    vraie équivalence sur "Rang produit" (`, ,` vs `,,` autour d'un argument
    RANKX vide) ; recalculer avec le normalize_expr corrigé la retrouve."""
    updated = dict(existing)
    n_changed = 0
    for nid, node in updated.items():
        quality = node.get("quality", [])
        old_entries = [q for q in quality if q.get("label") == OLD_LABEL]
        if not old_entries:
            continue

        name_partners: set[tuple[str, str]] = set()
        for q in old_entries:
            note = q.get("note") or ""
            if note.startswith("Même nom que"):
                name_partners |= set(_NOTE_PARTNER_RE.findall(note))

        if not name_partners:
            continue

        own_expr = normalize_expr(node.get("sql", "")) if node.get("sqlKind") == "dax" else None
        divergent: list[tuple[str, str]] = []
        consistent: list[tuple[str, str]] = []
        for measure, model in sorted(name_partners):
            partner = updated.get(f"dax_measure_{slugify(model)}_{slugify(measure)}")
            partner_expr = normalize_expr(partner.get("sql", "")) if partner and partner.get("sqlKind") == "dax" else None
            if own_expr is not None and own_expr == partner_expr:
                consistent.append((measure, model))
            else:
                divergent.append((measure, model))
        new_entries = []
        if divergent:
            note = "Formule différente de " + ", ".join(f'"{m}" ({s})' for m, s in divergent) + " sous le même nom — vérifier laquelle fait foi."
            new_entries.append({"label": LABEL_DIVERGENT, "status": "fail", "note": note})
        if consistent:
            note = "Même nom et même formule que " + ", ".join(f'"{m}" ({s})' for m, s in consistent) + " — duplication cohérente, à centraliser si possible."
            new_entries.append({"label": LABEL_CONSISTENT, "status": "warn", "note": note})

        remaining = [q for q in quality if q.get("label") != OLD_LABEL]
        node["quality"] = remaining + new_entries
        n_changed += 1
    return updated, n_changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from-json", type=Path, action="append", dest="dumps", help="Export JSON d'un modèle Power BI — répéter pour comparer plusieurs modèles (au moins 2)")
    parser.add_argument("--migrate", action="store_true", help="Reconcilie les annotations déjà posées par l'ancien schéma, sans --from-json")
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML, help="index.html à mettre à jour (avec --apply)")
    parser.add_argument("--snapshots", type=Path, default=SNAPSHOTS_DIR, help="Dossier des instantanés historisés")
    parser.add_argument("--apply", action="store_true", help="Écrit les annotations dans index.html (sans, affiche juste le rapport)")
    args = parser.parse_args()

    if args.migrate:
        if args.dumps:
            raise SystemExit("--migrate ne prend pas de --from-json (il relit les annotations déjà dans le HTML).")
        existing = load_existing_real_nodes(args.html)
        updated, n_changed = migrate_existing(existing)
        if n_changed == 0:
            print("Rien à migrer — aucune annotation à l'ancien format trouvée.")
            return
        print(f"{n_changed} nœud(s) reconcilié(s) vers le nouveau schéma (fail si divergence, warn sinon).")
        if not args.apply:
            print("\n(dry-run — relancer avec --apply pour écrire index.html)")
            return
        generated_at = datetime.now(timezone.utc).isoformat()
        splice(args.html, "AUTO-GENERATED", to_js_const("realNodes", updated) + "\n" + to_js_const("REAL_GENERATED_AT", generated_at))
        is_new = save_snapshot(updated, generated_at, args.snapshots, "Migration des annotations de mesures dupliquées vers le schéma cohérent/divergent")
        splice(args.html, "SNAPSHOTS", build_snapshots_block(args.snapshots))
        print(f"\nOK — {args.html} mis à jour.")
        print(f"instantané {'ajouté' if is_new else 'déjà présent'} pour {generated_at}")
        return

    if not args.dumps or len(args.dumps) < 2:
        raise SystemExit("Fournir au moins deux --from-json pour comparer (ou --migrate pour reconcilier l'existant).")

    dumps = [json.loads(p.read_text(encoding="utf-8")) for p in args.dumps]
    results = find_duplicates(dumps)

    if not results:
        print("Aucune mesure dupliquée trouvée (même nom, au moins deux modèles) entre les modèles fournis.")
        return

    n_fail = sum(1 for r in results if r["status"] == "fail")
    n_warn = sum(1 for r in results if r["status"] == "warn")
    print(f"{len(results)} annotation(s) trouvée(s) — {n_fail} divergence(s) réelle(s), {n_warn} duplication(s) cohérente(s) :")
    for r in results:
        marker = "‼" if r["status"] == "fail" else "·"
        print(f"  {marker} [{r['nid']}] {r['label']} — {r['note']}")

    if not args.apply:
        print("\n(dry-run — relancer avec --apply pour annoter index.html)")
        return

    existing = load_existing_real_nodes(args.html)
    updated, n_added = annotate_nodes(existing, results)
    if n_added == 0:
        print("Rien à écrire — annotations déjà posées par un run précédent.")
        return

    generated_at = datetime.now(timezone.utc).isoformat()
    splice(args.html, "AUTO-GENERATED", to_js_const("realNodes", updated) + "\n" + to_js_const("REAL_GENERATED_AT", generated_at))
    is_new = save_snapshot(updated, generated_at, args.snapshots, "Détection de mesures dupliquées Power BI")
    splice(args.html, "SNAPSHOTS", build_snapshots_block(args.snapshots))
    print(f"\nOK — {n_added} annotation(s) ajoutée(s), {args.html} mis à jour.")
    print(f"instantané {'ajouté' if is_new else 'déjà présent'} pour {generated_at}")


if __name__ == "__main__":
    main()
