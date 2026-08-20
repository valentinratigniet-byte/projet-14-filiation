# Projet 14 — Filiation : documentation vivante et interactive de traçabilité

> On demande souvent d'où vient un chiffre. La réponse habituelle est un
> classeur Excel qui date de six mois, ou un aller-retour avec l'équipe data.
> **Filiation** répond directement dans l'outil : on clique sur n'importe quel
> élément — un indicateur, une colonne, une table — et on voit sa formule ou
> son SQL, puis on remonte, niveau par niveau, jusqu'à la donnée brute et sa
> source. Le [Projet 11](../projet-11-gouvernance) documente déjà le lignage
> techniquement (dbt docs, pensé pour l'équipe data) ; celui-ci l'expose sous
> une forme cliquable, pensée pour quelqu'un qui ne lit pas de DAG.

**Sommaire** : [Ce que fait le projet](#-ce-que-fait-le-projet) ·
[Rôles](#-rôles-simulation-de-visibilité) ·
[Résultats chiffrés](#-résultats-chiffrés) ·
[Contenu](#️-contenu) ·
[Reprise rapide](#-reprise-rapide-après-une-pause) ·
[Lancer / régénérer](#-lancer--régénérer) ·
[Valise de détection](#-valise-de-détection--auditer-une-base-sans-projet-dbt) ·
[Limites assumées](#️-limites-assumées) ·
[Feuille de route →](ROADMAP.md)

## 🔁 Reprise rapide (après une pause)

1. **Base Postgres du Projet 10** (nécessaire pour le jeu "Projet réel" et
   pour `scan_database.py`) : `docker ps --filter name=p07_ecommerce_db` —
   si arrêté, `docker start p07_ecommerce_db` (port 5433, identifiants de
   démo non secrets dans
   [`projet-10-pipeline-elt/dbt_ecommerce/profiles.yml`](../projet-10-pipeline-elt/dbt_ecommerce/profiles.yml)).
2. **Dépendances Python** (une fois par environnement) :
   `pip install -r requirements.txt`.
3. **Vérifier que tout tourne encore** :
   `python scripts/extract_filiation.py` puis ouvrir `index.html` — si la
   page est blanche, vérifier la console navigateur avant toute autre chose
   (voir historique de commits : une régression silencieuse s'est déjà
   produite ici, cause et correctif dans le commit
   `fix: page blanche depuis l'ajout des rôles (erreur JS au chargement)`).
4. **Ce qui reste à faire** : [ROADMAP.md](ROADMAP.md) — raccordement aux
   bases de données (durcissement), complétude des informations extraites,
   et extension du lignage jusqu'aux mesures/colonnes calculées Power BI.

## 🧬 Ce que fait le projet

Une page unique (`index.html`, aucune dépendance) avec deux jeux de données,
au choix dans la barre latérale :

- **Démo** — un scénario fictif (marge brute, CAC, taux de conversion...) sur
  deux domaines (Ventes/Marketing), pour démontrer le concept sans dépendre
  d'un vrai projet.
- **Projet réel** — introspecté depuis le [Projet 10](../projet-10-pipeline-elt)
  (`dbt_ecommerce`) : rien n'est inventé, tout vient de `manifest.json` /
  `catalog.json` / `run_results.json`.

```mermaid
flowchart LR
    subgraph P10["Projet 10 — dbt_ecommerce"]
        MAN["manifest.json<br/>+ catalog.json<br/>+ run_results.json"]
    end
    MAN -->|"extract_filiation.py<br/>(+ sqlglot pour le SQL compilé)"| JS["realNodes + SNAPSHOTS<br/>(JS)"]
    JS -->|régénère| HTML["index.html<br/>(Filiation)"]
    JS -->|historise| SNAP[("snapshots/*.json")]
    SNAP -.->|vue Dérive| HTML
    HTML -->|clic formule/SQL/colonne| HTML

    style HTML fill:#137A8B,color:#fff
    style MAN fill:#E4A93C,color:#1a1a1a
```

Quatre façons de regarder le lignage, dans le même outil :

1. **Fiche** — un nœud à la fois : formule ou SQL réel (colonnes cliquables),
   propriétaire/fraîcheur, qualité, dépendances directes, et pour le jeu réel
   la structure de la table (colonnes + types + tests) et, colonne par
   colonne, **la colonne source exacte dont elle dérive** — lignage
   colonne-à-colonne réel, calculé par [sqlglot](https://github.com/tobymao/sqlglot)
   sur le SQL compilé (pas juste "ce modèle dépend de ce modèle").
2. **Graphe complet** — vue d'ensemble zoomable de tout le lignage (layout en
   couches + heuristique anti-croisements), et une case **Analyse d'impact**
   qui surligne en cascade tout ce qui dépend d'un nœud sélectionné.
3. **Dérive** — compare deux instantanés historisés (`snapshots/*.json`) et
   liste ce qui a changé : modèles/sources ajoutés ou supprimés, colonnes
   ajoutées/supprimées/retypées, tests dbt ajoutés ou supprimés.
4. **Systèmes** — vue par système source (ex. "Postgres — ecommerce") : ses
   tables, leur volumétrie réelle (comptage direct en base), combien
   d'éléments en dépendent en aval, et un mini schéma relationnel entre ses
   tables (relations inférées par convention de nommage — ce projet ne
   déclare aucune contrainte FK en base, vérifié via `information_schema`).

Là où dbt n'a pas de description, la page l'affiche honnêtement plutôt que
d'improviser un texte.

## 🎭 Rôles (simulation de visibilité)

Un sélecteur en haut de la barre latérale change ce qui est visible :
**Administrateur**/**Informatique** (accès complet), **Exploitation**
(qualité/fraîcheur/systèmes, pas la logique de calcul), **PDG** (uniquement
les indicateurs business, type `metric` — sur le projet réel, qui n'a encore
aucun KPI modélisé, ce rôle voit un état vide honnête plutôt qu'un écran
blanc), **RH** (uniquement les éléments tagués "Donnée personnelle (RGPD)" —
`src_customer`/`stg_customers`/`dim_customer` sur le projet réel, taggés
automatiquement dès qu'une colonne `email` est détectée). Les références vers
un élément non accessible restent visibles mais verrouillées (🔒), pour
montrer *qu'*il existe une dépendance sans en révéler le contenu.

**Ce n'est pas un vrai contrôle d'accès** : cette page est un fichier
statique sans backend ni authentification — n'importe qui peut lire le code
source et voir toutes les données quel que soit le rôle affiché. C'est une
simulation pédagogique de ce à quoi ressemblerait un vrai portail avec RBAC
côté serveur.

Toute modification passe par un humain qui relit, jamais par une écriture
depuis l'outil — il n'y a pas de backend, pas d'identifiants stockés, pas de
chemin d'écriture caché :

- Sur la démo (systèmes fictifs), un bouton maquette rappelle le principe.
- Sur le projet réel, chaque table brute affiche une requête
  `SELECT ... LIMIT 20` prête à copier (lecture seule), et un **modèle** de
  correction (`UPDATE ... set <colonne> = <nouvelle_valeur> where
  <condition_precise>`) dont les placeholders sont volontairement invalides —
  un copier-coller sans adaptation échoue en base plutôt que de modifier
  toutes les lignes en silence.
- Chaque modèle dbt affiche un lien **"Signaler un problème"** qui ouvre une
  issue GitHub pré-remplie sur le fichier exact
  (`models/marts/fct_sales.sql`, etc.) — la correction d'un KPI/modèle passe
  par une PR revue, jamais par une écriture live que dbt écraserait de toute
  façon au run suivant.

Principe de gouvernance détaillé dans le
[Projet 11](../projet-11-gouvernance) : un ERP applique des règles métier
qu'une écriture directe en base contournerait, et l'authentification règle
qui peut agir — pas si l'action est sûre.

## 📊 Résultats chiffrés

| Jeu de données | Nœuds | Détail |
|---|---|---|
| Démo (fictif) | 15 | 2 domaines, 5 niveaux de profondeur |
| Projet réel (dbt_ecommerce) | 13 | 5 sources + 4 staging + 3 dimensions + 1 fait |
| Tests dbt affichés (jeu réel) | 28 | statut réel du dernier `dbt run` — 28/28 PASS |
| Colonnes avec lignage colonne-à-colonne | 33 | résolu par sqlglot sur le SQL compilé, y compris à travers CTE/joins/`generate_series` |
| Instantanés historisés | 2 | 1 extraction réelle + 1 exemple simulé (illustratif, pour démontrer la vue Dérive) |
| Lignes en base, couche `raw` (projet réel) | 168 741 | comptage réel via psycopg2, pas une estimation — `fct_sales` seul : 121 331 |
| Relations inférées | 4 | convention de nommage `xxx_id` → table `xxx`, sur les 5 tables `raw` (1 système) |

## 🗂️ Contenu

```
projet-14-filiation/
├── README.md
├── ROADMAP.md                     ← ce qui reste à faire (3 chantiers priorisés)
├── index.html                    ← l'outil, page unique auto-suffisante
├── requirements.txt               ← sqlglot, sqlalchemy (optionnels selon le script utilisé)
├── snapshots/                     ← historique d'extractions (pour la vue Dérive)
└── scripts/
    ├── extract_filiation.py      ← régénère index.html + historise un instantané depuis un target/ dbt
    └── scan_database.py          ← "valise de détection" : scanne n'importe quelle base, sans dbt
```

## 🚀 Lancer / régénérer

Ouvrir `index.html` dans un navigateur — aucune dépendance, aucun serveur.

Pour rafraîchir le jeu "Projet réel" après un changement dans le Projet 10 :

```bash
cd projet-10-pipeline-elt/dbt_ecommerce
dbt run && dbt test && dbt docs generate   # régénère target/manifest.json, catalog.json, run_results.json

cd ../../projet-14-filiation
pip install -r requirements.txt            # sqlglot, une fois
python scripts/extract_filiation.py        # relit target/, met à jour index.html, historise un instantané
```

Chaque exécution ajoute un instantané dans `snapshots/` (dédupliqué sur le
`generated_at` du manifest) — après deux `dbt run` réels, la vue Dérive
compare directement le vrai avant/après au lieu de l'exemple simulé fourni.

Le script accepte `--target` (autre dossier `target/` dbt), `--html` (autre
fichier à mettre à jour) et `--label` (nom lisible de l'instantané) pour
pointer vers un autre projet dbt.

## 🧳 Valise de détection — auditer une base sans projet dbt

`scripts/scan_database.py` scanne **n'importe quelle base** (Postgres,
MySQL, SQLite, SQL Server...) directement, sans dépendre d'un projet dbt —
utile pour un premier audit d'un système inconnu (ERP client, base legacy...).
Toujours en lecture seule (introspection + `SELECT`) :

```bash
export DATABASE_URL="postgresql://user:pass@host:port/db"   # jamais en argument (historique shell)
python scripts/scan_database.py --schemas public --label "ERP client X"
```

Détecte automatiquement : tables, colonnes et types, volumétrie réelle,
clés étrangères réelles si déclarées (sinon relations inférées par
convention de nommage, comme pour le projet réel), et des contrôles de
qualité calculés en direct (valeurs non nulles, unicité des clés simples —
une clé composite n'est jamais testée colonne par colonne). Alimente le même
`index.html` que `extract_filiation.py` (mêmes marqueurs, même historisation
dans `snapshots/`) — c'est la même appli, juste une autre source.

**Ce que ce script ne fait pas** : il n'écrit jamais dans la base scannée, ne
stocke aucun identifiant (uniquement `$DATABASE_URL`, jamais écrit dans
`index.html` ni dans un instantané — seuls le dialecte et le nom de la base
apparaissent), et reste un script qu'on lance soi-même — pas un service
hébergé qui garderait des connexions à plusieurs systèmes clients. Cette
version-là (portail multi-ERP avec gestion de connexions) est un chantier
à part, avec un tout autre modèle de risque (coffre-fort à secrets, contrôle
d'accès réseau) — pas construite ici pour l'instant.

## ⚠️ Limites assumées

`index.html` est une page statique : elle ne se connecte pas à une base en
direct (pas de backend). "Se met à jour toute seule" veut dire *relancer le
script après chaque `dbt run`*, pas une synchronisation live — pour ça il
faudrait une vraie application avec un backend interrogeant la base à chaque
chargement.

Ce que les scripts d'extraction ne couvrent pas encore (commentaires
déclarés en base, vues, index, contraintes CHECK, deuxième moteur de base
testé au-delà de Postgres...) et l'extension du lignage jusqu'aux mesures et
colonnes calculées Power BI : voir [ROADMAP.md](ROADMAP.md).
