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
[Power BI](#-power-bi--mesures-et-colonnes-calculées-dax) ·
[Pipelines n8n](#-pipelines-n8n) ·
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
4. **Ce qui reste à faire** : [ROADMAP.md](ROADMAP.md) — les 4 chantiers
   sont substantiellement bouclés, il ne reste que des points annexes
   (exécutions/statuts n8n live, vues matérialisées/triggers côté bases...).

## 🧬 Ce que fait le projet

Une page unique (`index.html`, aucune dépendance) avec deux jeux de données,
au choix dans la barre latérale :

- **Démo** — un scénario fictif (marge brute, EBITDA, taux de turnover, ROI,
  score de qualité des données...) sur sept domaines (Ventes, Marketing,
  Finance, RH, Gestion de projets, Investissement, Data), pour démontrer le
  concept sans dépendre d'un vrai projet.
- **Projet réel** — rien n'est inventé, tout vient d'une introspection
  réelle, fusionnée depuis **5 systèmes indépendants** : le
  [Projet 10](../projet-10-pipeline-elt) (`dbt_ecommerce`, manifest/catalog/
  run_results), deux bases du projet partagé
  [projet-baptiste-valentin](../../projet-baptiste-valentin)
  (`bv-postgres-dbtdev`, `bv-mysql-crm`), deux modèles Power BI réels
  ([Projet 09](../projet-09-dashboard-powerbi),
  [Projet 13](../projet-13-entrepot-central-bigquery)) et 5 workflows n8n
  réels du même projet partagé — 78 nœuds au total, un système par carte
  dans la vue Systèmes.

```mermaid
flowchart LR
    subgraph P10["Projet 10 — dbt_ecommerce"]
        MAN["manifest.json<br/>+ catalog.json<br/>+ run_results.json"]
    end
    subgraph DB["Bases (Postgres/MySQL/SQL Server/SQLite)"]
        SQLA["SQLAlchemy<br/>introspection"]
    end
    subgraph PBI["Power BI (Projets 09 + 13)"]
        MCP["MCP powerbi-modeling<br/>(mesures + colonnes DAX)"]
    end
    subgraph N8N["n8n (bv-dataplatform)"]
        WF["workflows/*.json<br/>déjà versionnés"]
    end

    MAN -->|"extract_filiation.py<br/>(+ sqlglot)"| JS["realNodes + SNAPSHOTS<br/>(JS, toujours fusionné)"]
    SQLA -->|scan_database.py| JS
    MCP -->|"extract_powerbi.py<br/>+ find_duplicate_powerbi_measures.py"| JS
    WF -->|extract_n8n.py| JS
    JS -->|régénère| HTML["index.html<br/>(Filiation)"]
    JS -->|historise| SNAP[("snapshots/*.json")]
    SNAP -.->|vue Dérive| HTML
    HTML -->|clic formule/SQL/DAX/colonne| HTML

    style HTML fill:#137A8B,color:#fff
    style MAN fill:#E4A93C,color:#1a1a1a
```

Cinq façons de regarder le lignage, dans le même outil :

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
4. **Systèmes** — vue par système source (ex. "Postgres — ecommerce",
   "Power BI — Dashboard entrepot", "n8n — bv-dataplatform") : ses
   tables/mesures/workflows, leur volumétrie réelle (comptage direct en
   base quand ça s'applique), combien d'éléments en dépendent en aval, et
   un mini schéma relationnel entre ses tables (relations inférées par
   convention de nommage — ce projet ne déclare aucune contrainte FK en
   base, vérifié via `information_schema`).
5. **Assistant** — poser une question en langage naturel sur un élément du
   graphe (ex. "d'où vient cette donnée ?"), répondue par un LLM local
   (`bv-ollama`) dont le contexte se limite au sous-graphe pertinent
   (l'élément + son voisinage direct), jamais tout le graphe.

Là où dbt n'a pas de description, la page l'affiche honnêtement plutôt que
d'improviser un texte.

## 🎭 Rôles (simulation de visibilité)

Un sélecteur en haut de la barre latérale change ce qui est visible :
**Administrateur**/**Informatique** (accès complet), **Exploitation**
(qualité/fraîcheur/systèmes, pas la logique de calcul), **PDG** (uniquement
les indicateurs business — type `metric` sur la démo, et les 34 mesures DAX
réelles `dax-measure` sur le projet réel depuis le chantier Power BI ; avant
ça, ce rôle affichait un état vide honnête plutôt qu'un écran blanc, faute
de KPI modélisé), **RH** (uniquement les éléments tagués "Donnée personnelle
(RGPD)" —
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
| Démo (fictif) | 76 | 7 domaines (Ventes, Marketing, Finance, RH, Gestion, Investissement, Data), 5 niveaux de profondeur |
| Projet réel (dbt_ecommerce) | 13 | 5 sources + 4 staging + 3 dimensions + 1 fait |
| Tests dbt affichés (jeu réel) | 28 | statut réel du dernier `dbt run` — 28/28 PASS |
| Colonnes avec lignage colonne-à-colonne | 33 | résolu par sqlglot sur le SQL compilé, y compris à travers CTE/joins/`generate_series` |
| Instantanés historisés | 2 | 1 extraction réelle + 1 exemple simulé (illustratif, pour démontrer la vue Dérive) |
| Lignes en base, couche `raw` (projet réel) | 168 741 | comptage réel via psycopg2, pas une estimation — `fct_sales` seul : 121 331 |
| Relations inférées | 4 | convention de nommage `xxx_id` → table `xxx`, sur les 5 tables `raw` (1 système) |
| Mesures DAX (Power BI, Projets 09 + 13) | 34 | extraites de 2 modèles réels via MCP `powerbi-modeling` — 17 duplications de nom détectées entre les deux, 15 de formule |
| Workflows n8n (pipeline) | 5 | lus depuis `projet-baptiste-valentin/n8n/workflows/*.json`, aucune connexion live |
| Nœuds réels au total (jeu "Projet réel", fusionné) | 78 | dbt_ecommerce + bv-postgres-dbtdev + bv-mysql-crm + Power BI Projets 09+13 (34 mesures) + n8n (5 pipelines) |

## 🗂️ Contenu

```
projet-14-filiation/
├── README.md
├── ROADMAP.md                     ← historique des 4 chantiers (tous substantiellement bouclés) et ce qui reste
├── index.html                    ← l'outil, page unique auto-suffisante
├── requirements.txt               ← sqlglot, sqlalchemy (optionnels selon le script utilisé)
├── snapshots/                     ← historique d'extractions (pour la vue Dérive)
├── connections.example.yml        ← template pour connections.yml (gitignoré) : alias -> nom de var d'env
└── scripts/
    ├── extract_filiation.py      ← régénère index.html + historise un instantané depuis un target/ dbt
    ├── scan_database.py          ← "valise de détection" : scanne n'importe quelle base, sans dbt
    ├── extract_powerbi.py        ← ajoute mesures/colonnes calculées DAX depuis un export JSON (MCP powerbi-modeling)
    ├── powerbi_export.example.json      ← schéma attendu par extract_powerbi.py --from-json
    ├── find_duplicate_powerbi_measures.py  ← annote les mesures dupliquées entre plusieurs .pbix
    ├── extract_n8n.py            ← ajoute des nœuds pipeline depuis des workflows n8n versionnés en JSON
    ├── suggest_descriptions.py   ← descriptions suggérées par LLM local (bv-ollama), jamais imposées
    ├── test_scan_database.py     ← self-check scan_database.py (SQLite jetable)
    ├── test_extract_powerbi.py   ← self-check extract_powerbi.py (parsing DAX, sans modèle live)
    ├── test_find_duplicate_powerbi_measures.py  ← self-check détection de doublons (dumps synthétiques)
    └── test_extract_n8n.py       ← self-check extract_n8n.py (workflows synthétiques)
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

## 📊 Power BI — mesures et colonnes calculées DAX

Complète la chaîne base → dbt → Power BI : mesures et colonnes calculées
DAX du modèle sémantique, avec lignage textuel (`Table[Colonne]` → table
source, `[Mesure]` → autre mesure du modèle) jusque dans le graphe et le
texte de la formule (références cliquables). Deux garde-fous best-effort,
en pastille qualité comme le reste de l'outil (bouton IA "Expliquer
l'impact" inclus) :

- une mesure qui référence plusieurs tables sans `CALCULATE`/`FILTER`/`ALL`
  explicite est signalée (risque de contexte de filtre mal maîtrisé) ;
- une colonne calculée qui référence une autre colonne calculée est
  signalée (risque de cascade au refresh).

**Contrairement à `extract_filiation.py` et `scan_database.py`, ce script ne
se connecte pas lui-même à un modèle Power BI** — impossible en Python pur,
seul le MCP `powerbi-modeling` (Tabular Object Model) sait parler à un
modèle live, et il n'est accessible que depuis une session Claude Code avec
le `.pbix` ouvert dans Power BI Desktop :

```bash
# 1. Ouvrir le .pbix dans Power BI Desktop.
# 2. Dans une session Claude Code (MCP powerbi-modeling) : ListLocalInstances
#    + Connect, puis table/measure/column_operations List+Get, écrire le
#    résultat en JSON — schéma dans scripts/powerbi_export.example.json.
# 3.
python scripts/extract_powerbi.py --from-json powerbi_export.json
```

Toujours additif (contrairement à `scan_database.py` sans `--merge`) : un
modèle Power BI vient s'ajouter aux nœuds réels déjà présents, il ne les
remplace jamais.

**Mesures dupliquées entre rapports** : une fois deux modèles (ou plus)
extraits, `scripts/find_duplicate_powerbi_measures.py` compare leurs exports
JSON et détecte, indépendamment, une même mesure recopiée entre rapports
(même nom) ou une même logique sous un autre nom (même formule DAX
normalisée) — annote les nœuds déjà présents (aucun nouveau nœud) avec un
contrôle qualité, réutilisant la même pastille + bouton IA que le reste de
l'outil :

```bash
python scripts/extract_powerbi.py --from-json projet09.json
python scripts/extract_powerbi.py --from-json projet13.json
python scripts/find_duplicate_powerbi_measures.py --from-json projet09.json --from-json projet13.json --apply
```

Testé en réel entre les Projets 09 et 13 (17 mesures chacun, même socle) :
17 duplications de nom, 15 de formule — les 2 écarts (`CA moyenne 3M`,
`Rang produit`) ont le même nom mais une logique réellement différente
entre les deux rapports, exactement le genre de dérive silencieuse que ce
script existe pour repérer.

## 🔗 Pipelines n8n

Un nœud par workflow n8n réel (`type: "pipeline"`) : déclencheur, étapes
dans l'ordre de l'export, lignage textuel best-effort vers les tables
dbt/base déjà présentes (regex sur les requêtes Postgres des workflows), et
un contrôle qualité tiré d'une vraie règle métier du projet partagé (accès
Postgres limité à la couche Gold `public_marts`, jamais direct sur
`raw`/`erp_migre`).

**Contrairement à Power BI, aucun MCP nécessaire** : les workflows sont déjà
versionnés en JSON (`projet-baptiste-valentin/n8n/workflows/*.json`),
`scripts/extract_n8n.py` les lit directement — pas de connexion, pas
d'identifiant, l'API n8n live reste inaccessible (authentification requise,
bloquée par le classificateur de commandes) mais n'est pas nécessaire ici.

```bash
python scripts/extract_n8n.py   # --workflows-dir pour un autre dossier
```

## ⚠️ Limites assumées

`index.html` est une page statique : elle ne se connecte pas à une base en
direct (pas de backend). "Se met à jour toute seule" veut dire *relancer le
script après chaque `dbt run`*, pas une synchronisation live — pour ça il
faudrait une vraie application avec un backend interrogeant la base à chaque
chargement.

Chaque script d'extraction a son propre modèle de connexion — pas de
prétention à l'uniformité là où la réalité diverge : `extract_filiation.py`
lit un `target/` dbt compilé, `scan_database.py` se connecte en direct à
n'importe quelle base SQLAlchemy, `extract_n8n.py` lit des workflows déjà
versionnés en JSON, et `extract_powerbi.py` (seul cas) **ne se connecte pas
lui-même** à un modèle Power BI — impossible en Python pur, il transforme un
export JSON produit via le MCP `powerbi-modeling`, accessible uniquement
depuis une session Claude Code avec le `.pbix` ouvert. Voir la section
[Power BI](#-power-bi--mesures-et-colonnes-calculées-dax) plus bas. Le
lignage `Table[Colonne]` fonctionne par correspondance de **nom** avec les
nœuds déjà présents, pas par identité physique garantie — deux systèmes
différents partageant un nom de table (ex. `dim_date`) sont ambigus, résolus
arbitrairement (dernier trouvé), comme pour le lignage SQL clickable existant
en cas de token dupliqué.

`scan_database.py` (SQLAlchemy) ne couvre que des bases **relationnelles**
(Postgres/MySQL/SQL Server/SQLite...) — MongoDB (`bv-mongo-logs` sur ce
poste, par exemple) est hors périmètre de l'outil actuel, pas seulement hors
périmètre "projet partagé". `extract_n8n.py` lit la définition **statique**
des workflows (étapes, requêtes), pas leurs exécutions ou statuts en
direct — l'API n8n live existe mais reste derrière une authentification
jamais résolue dans ce projet (voir [ROADMAP.md](ROADMAP.md), chantier 4).

Reste à faire : voir [ROADMAP.md](ROADMAP.md).
