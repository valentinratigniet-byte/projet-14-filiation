"""Propose des descriptions pour les nœuds réels sans documentation, via un
LLM local (Ollama). Ne modifie JAMAIS `description` : écrit uniquement un
champ `aiSuggestion` séparé, affiché dans l'outil avec un badge explicite
"Suggestion IA, non vérifiée" — à un humain de la relire et de la reprendre
dans la vraie documentation (commentaire dbt, COMMENT ON, etc.) s'il la juge
correcte. Voir ROADMAP.md, chantier 4.

Usage :
    python scripts/suggest_descriptions.py [--html INDEX_HTML] [--model llama3.2:3b]
                                            [--url http://localhost:11434] [--limit N] [--force]

Lecture seule côté base : ne fait aucune requête SQL, travaille uniquement à
partir du `realNodes` déjà présent dans index.html (généré par
extract_filiation.py / scan_database.py). Le LLM peut se tromper (nom de
colonne ambigu, faux sens) — c'est précisément pour ça que la suggestion
reste distincte de la description officielle.
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

from extract_filiation import splice, to_js_const
from scan_database import load_existing_real_nodes

sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_HTML = Path(__file__).resolve().parent.parent / "index.html"
DEFAULT_MODEL = "llama3.2:3b"
DEFAULT_URL = "http://localhost:11434"

GENERIC_MARKERS = ("Aucune description renseignée", "aucune documentation associée")

PROMPT_TEMPLATE = """Tu es un assistant qui documente une base de données pour des utilisateurs non techniques.
Décris en une seule phrase claire et concise, en français et sans jargon technique, ce que contient probablement cette table, à partir de son nom et de ses colonnes. Si le nom est ambigu, reste prudent et général plutôt que d'inventer un détail que tu ne peux pas déduire.

Table : {name} (domaine : {domain})
Colonnes : {columns}

Réponds uniquement avec la phrase de description, sans préambule ni guillemets."""


def needs_suggestion(node: dict, force: bool) -> bool:
    if node.get("type") not in ("raw", "derived"):
        return False
    if not force and node.get("aiSuggestion"):
        return False
    desc = node.get("description", "")
    return any(marker in desc for marker in GENERIC_MARKERS)


def build_prompt(node: dict) -> str:
    columns = ", ".join(f"{c['name']} ({c['type']})" for c in node.get("columns", [])) or "(inconnues)"
    return PROMPT_TEMPLATE.format(name=node.get("name", "?"), domain=node.get("domain", "?"), columns=columns)


def call_ollama(prompt: str, model: str, base_url: str) -> str:
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/api/generate", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())["response"].strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML, help="index.html à mettre à jour")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Modèle Ollama à utiliser")
    parser.add_argument("--url", default=DEFAULT_URL, help="URL de base du serveur Ollama")
    parser.add_argument("--limit", type=int, default=None, help="Nombre maximum de nœuds à traiter")
    parser.add_argument("--force", action="store_true", help="Régénérer même les nœuds ayant déjà une suggestion")
    args = parser.parse_args()

    nodes = load_existing_real_nodes(args.html)
    if not nodes:
        raise SystemExit(f"Aucun realNodes trouvé dans {args.html} — lancer extract_filiation.py ou scan_database.py d'abord.")

    targets = [nid for nid, n in nodes.items() if needs_suggestion(n, args.force)]
    if args.limit:
        targets = targets[: args.limit]

    if not targets:
        print("Rien à faire — tous les nœuds documentables ont déjà une description ou une suggestion.")
        return

    print(f"{len(targets)} nœud(s) sans description à traiter via {args.model} ({args.url})…")
    done = 0
    for nid in targets:
        node = nodes[nid]
        try:
            suggestion = call_ollama(build_prompt(node), args.model, args.url)
        except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as e:
            print(f"  ✗ {nid} : Ollama injoignable ou réponse invalide ({e}) — laissé de côté.")
            continue
        node["aiSuggestion"] = suggestion
        done += 1
        print(f"  ✓ {nid} : {suggestion}")

    if done == 0:
        print("Aucune suggestion générée — vérifier qu'Ollama tourne bien sur", args.url)
        return

    html = args.html.read_text(encoding="utf-8")
    generated_at_m = re.search(r'const REAL_GENERATED_AT = "([^"]*)"', html)
    lines = [to_js_const("realNodes", nodes)]
    if generated_at_m:
        lines.append(to_js_const("REAL_GENERATED_AT", generated_at_m.group(1)))
    splice(args.html, "AUTO-GENERATED", "\n".join(lines))

    print(f"OK — {done} suggestion(s) ajoutée(s), {args.html} mis à jour.")


if __name__ == "__main__":
    main()
