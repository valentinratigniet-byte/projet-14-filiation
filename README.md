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
[Ergonomie](#ergonomie) ·
[Rôles](#-rôles-simulation-de-visibilité) ·
[Résultats chiffrés](#-résultats-chiffrés) ·
[Contenu](#️-contenu) ·
[Reprise rapide](#-reprise-rapide-après-une-pause) ·
[Lancer / régénérer](#-lancer--régénérer) ·
[Valise de détection](#-valise-de-détection--auditer-une-base-sans-projet-dbt) ·
[Power BI](#-power-bi--mesures-et-colonnes-calculées-dax) ·
[Pipelines n8n & Prefect](#-pipelines-n8n--prefect) ·
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
   sont bouclés ; 3 points restent délibérément écartés, pas oubliés
   (domaine Data du jeu démo, quelques introspections base moins
   prioritaires, portail hébergé multi-ERP). Deux rounds "post-roadmap"
   (ergonomie, dogfooding par rôle, filtres, favoris, automatisation n8n)
   ont continué depuis — voir le bas de ROADMAP.md pour le détail.

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
  réels du même projet partagé, et 2 flows Prefect réels du portfolio lui-même
  (Projets 04 et 10) — 80 nœuds au total, un système par carte dans la vue
  Systèmes.

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
    subgraph PREF["Prefect (local, Projets 04+10)"]
        PC["client Prefect<br/>(flows/runs/tasks)"]
    end

    MAN -->|"extract_filiation.py<br/>(+ sqlglot)"| JS["realNodes + SNAPSHOTS<br/>(JS, toujours fusionné)"]
    SQLA -->|scan_database.py| JS
    MCP -->|"extract_powerbi.py<br/>+ find_duplicate_powerbi_measures.py"| JS
    WF -->|extract_n8n.py| JS
    PC -->|extract_prefect.py| JS
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
4. **Systèmes** — une carte **Alertes qualité** (tous les contrôles non-ok,
   triés échecs puis avertissements, cliquables vers la fiche concernée —
   sans ça, la seule façon de les découvrir est de cliquer les nœuds un par
   un) puis une carte par système source (ex. "Postgres — ecommerce",
   "Power BI — Dashboard entrepot", "n8n — bv-dataplatform") : ses
   tables/mesures/workflows, leur volumétrie réelle (comptage direct en
   base quand ça s'applique), combien d'éléments en dépendent en aval, et
   un mini schéma relationnel entre ses tables (relations inférées par
   convention de nommage — ce projet ne déclare aucune contrainte FK en
   base, vérifié via `information_schema`).
5. **Assistant** — poser une question en langage naturel sur un élément du
   graphe (ex. "d'où vient cette donnée ?"), répondue par un LLM local
   (`bv-ollama`) dont le contexte se limite au sous-graphe pertinent
   (l'élément + son voisinage direct), jamais tout le graphe. Un statut
   ("🟢 disponible" / "🔴 injoignable") est vérifié une fois au chargement —
   si la page est ouverte en double-cliquant le fichier (`file://`), Ollama
   bloque la requête en CORS même s'il tourne ; la servir localement
   (`python -m http.server` puis `http://127.0.0.1:PORT/index.html`) suffit,
   son origine par défaut autorise déjà `localhost`/`127.0.0.1`.

Là où dbt n'a pas de description, la page l'affiche honnêtement plutôt que
d'improviser un texte.

### Ergonomie

Ajouté après la version fonctionnelle initiale, en pilotant l'outil comme
un vrai utilisateur (voir la section post-roadmap de
[ROADMAP.md](ROADMAP.md) pour le détail et les bugs trouvés en chemin) :

- **Recherche globale** (`/` ou bouton dans la barre latérale) — cherche
  parmi tous les éléments accessibles au rôle courant, tous domaines et
  systèmes confondus, classés par pertinence.
- **Historique de navigation** (précédent/suivant, boutons ← → en haut de
  la fiche) — distinct du fil d'Ariane, qui reste un chemin de dérivation.
- **État persistant** (`localStorage`) : jeu de données, rôle, dernier
  élément consulté, thème, éléments épinglés et récemment consultés — tout
  restauré au rechargement.
- **Éléments épinglés + récemment consultés** dans la barre latérale
  (bouton étoile sur chaque fiche).
- **Filtres à bascule unique/multiple** (Tout sélectionner/désélectionner)
  sur le domaine et le système dans le Graphe complet, et sur le système
  dans la vue Systèmes.
- **Minimap** et **survol pour isoler les liens** d'un nœud dans le Graphe
  complet — utile dès que le graphe devient dense (arêtes qui se
  croisent), le zoom seul n'y change rien.
- **Thème clair/sombre/automatique** (bouton dédié, persisté) et overlay
  d'aide clavier (`?`).
- **Sévérité visuelle** sur les alertes qualité (bandeau rouge/ambre selon
  fail/warn) et recherche/filtre sur la liste "Alertes qualité".

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
| Mesures DAX (Power BI, Projets 09 + 13) | 34 | extraites de 2 modèles réels via MCP `powerbi-modeling` — 17 mesures dupliquées entre les deux modèles sous le même nom, dont **2 divergent réellement** (formule différente, signalées "fail") et 15 sont cohérentes |
| Workflows n8n (pipeline) | 5 | lus depuis `projet-baptiste-valentin/n8n/workflows/*.json`, aucune connexion live |
| Nœuds réels au total (jeu "Projet réel", fusionné) | 80 | dbt_ecommerce + bv-postgres-dbtdev + bv-mysql-crm + Power BI Projets 09+13 (34 mesures) + n8n (5 pipelines) + Prefect (2 flows) |

## 🗂️ Contenu

```
projet-14-filiation/
├── README.md
├── ROADMAP.md                     ← historique des 4 chantiers (tous bouclés) et les 3 points restants (délibérément écartés)
├── index.html                    ← l'outil, page unique auto-suffisante
├── requirements.txt               ← sqlglot, sqlalchemy (optionnels selon le script utilisé)
├── snapshots/                     ← historique d'extractions (pour la vue Dérive)
├── connections.example.yml        ← template pour connections.yml (gitignoré) : alias -> nom de var d'env
└── scripts/
    ├── extract_filiation.py      ← régénère index.html + historise un instantané depuis un target/ dbt
    ├── scan_database.py          ← "valise de détection" : scanne n'importe quelle base, sans dbt
    ├── extract_powerbi.py        ← ajoute mesures/colonnes calculées DAX depuis un export JSON (MCP powerbi-modeling)
    ├── powerbi_export.example.json      ← schéma attendu par extract_powerbi.py --from-json
    ├── find_duplicate_powerbi_measures.py  ← annote les mesures dupliquées entre .pbix (warn si cohérentes, fail si formules divergentes)
    ├── extract_n8n.py            ← ajoute des nœuds pipeline depuis des workflows n8n versionnés en JSON
    ├── extract_prefect.py        ← ajoute des nœuds pipeline depuis le client Prefect local (connexion directe)
    ├── suggest_descriptions.py   ← descriptions suggérées par LLM local (bv-ollama), jamais imposées
    ├── test_scan_database.py     ← self-check scan_database.py (SQLite jetable)
    ├── test_extract_powerbi.py   ← self-check extract_powerbi.py (parsing DAX, sans modèle live)
    ├── test_find_duplicate_powerbi_measures.py  ← self-check détection de doublons (dumps synthétiques)
    ├── test_extract_n8n.py       ← self-check extract_n8n.py (workflows synthétiques)
    └── test_extract_prefect.py   ← self-check extract_prefect.py (fraîcheur/statut, sans connexion live)
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

**Mesures dupliquées entre rapports, sévérité différenciée** : une fois deux
modèles (ou plus) extraits, `scripts/find_duplicate_powerbi_measures.py`
compare leurs exports JSON et pose **une seule pastille consolidée par
mesure** — "warn" (mesure dupliquée mais cohérente : même nom, même formule
normalisée) ou "**fail**" (définitions divergentes : même nom, formule
**différente** — deux rapports affichent un chiffre sous le même nom sans
le calculer pareil, le vrai risque de gouvernance) :

```bash
python scripts/extract_powerbi.py --from-json projet09.json
python scripts/extract_powerbi.py --from-json projet13.json
python scripts/find_duplicate_powerbi_measures.py --from-json projet09.json --from-json projet13.json --apply
```

`--migrate` reconcilie a posteriori les anciennes annotations (schéma à
deux pastilles séparées, une version précédente de ce script) en
recalculant depuis le champ `sql` déjà embarqué sur chaque nœud, sans
requérir de nouveaux exports :

```bash
python scripts/find_duplicate_powerbi_measures.py --migrate --apply
```

Testé en réel entre les Projets 09 et 13 (17 mesures chacun, même socle) :
17 mesures partagent un nom entre les deux modèles, dont **2 divergent
réellement** (`CA moyenne 3M`, `Rang produit` au départ — puis correction
d'un faux positif de normalisation, voir plus bas) et 15 sont cohérentes.
Après correction de `normalize_expr` (un espacement autour d'une virgule,
`, ,` vs `,,` dans un `RANKX`, empêchait de reconnaître deux formules
DAX identiques), seule `CA moyenne 3M` reste une divergence authentique —
`Rang produit` a la même logique dans les deux modèles, juste un
espacement différent.

## 🔗 Pipelines n8n & Prefect

Un nœud `type: "pipeline"` par workflow n8n ou flow Prefect réel — même type
des deux côtés (deux orchestrateurs, un badge domaine + une carte Systèmes
distincts suffisent à les distinguer) :

- **n8n** : déclencheur, étapes dans l'ordre de l'export, lignage textuel
  best-effort vers les tables dbt/base déjà présentes (regex sur les
  requêtes Postgres des workflows), et un contrôle qualité tiré d'une vraie
  règle métier du projet partagé (accès Postgres limité à la couche Gold
  `public_marts`, jamais direct sur `raw`/`erp_migre`).
  **Aucun MCP nécessaire** : les workflows sont déjà versionnés en JSON
  (`projet-baptiste-valentin/n8n/workflows/*.json`), `scripts/extract_n8n.py`
  les lit directement — pas de connexion, pas d'identifiant, l'API n8n live
  reste inaccessible (authentification requise, bloquée par le
  classificateur de commandes) mais n'est pas nécessaire ici.
  ```bash
  python scripts/extract_n8n.py   # --workflows-dir pour un autre dossier
  ```
- **Prefect** : étapes de la dernière exécution, et un contrôle de
  **fraîcheur réel** (`warn` si la dernière exécution date de plus de 7
  jours, `fail` si son statut n'est pas `Completed`). `scripts/
  extract_prefect.py` **se connecte en direct** au client Prefect local
  (lecture seule, `read_flows`/`read_flow_runs`/`read_task_runs`) — Prefect
  n'exige pas d'authentification par mot de passe en usage local (profil
  "ephemeral", base SQLite dans `~/.prefect/`), contrairement à n8n.
  Nécessite le paquet `prefect`, absent du python système : s'exécute avec
  le venv de [Projet 10](../projet-10-pipeline-elt), pas avec celui des
  autres scripts de ce dossier.
  ```bash
  ../projet-10-pipeline-elt/.venv/Scripts/python.exe scripts/extract_prefect.py
  ```
  Extrait les 2 flows réels du portfolio (`elt-ecommerce` du Projet 10,
  `entrepot-etl` du Projet 04) avec leur vrai historique d'exécution.

**Watchdog n8n (2026-08-24)** : `filiation-derive-structurelle.json`, un
6e workflow versionné dans `projet-baptiste-valentin/n8n/workflows/` (même
convention que les 5 existants — webhook de déclenchement, nœud Postgres
via le credential partagé, `noOp` final documentant l'intégration à
brancher). Compte les tables des schémas `erp_migre`/`public_marts`/`raw`
sur `bv-postgres-dbtdev` (`information_schema`, portable) et compare au
dernier compte connu lors du dernier `scan_database.py --merge` (23) —
signale qu'une extraction est probablement nécessaire si ça diverge.
**Ne relance pas le pipeline lui-même** : le conteneur `bv-n8n` n'a accès
ni au système de fichiers de ce poste ni à Power BI Desktop, seulement à
`bv-postgres-dbtdev`/`bv-mysql-crm` (même réseau Docker). Non testé en
conditions réelles (pas d'accès interactif à l'UI/API n8n depuis une
session Claude Code) — à importer et vérifier avant activation.

## ⚠️ Limites assumées

`index.html` est une page statique : elle ne se connecte pas à une base en
direct (pas de backend). "Se met à jour toute seule" veut dire *relancer le
script après chaque `dbt run`*, pas une synchronisation live — pour ça il
faudrait une vraie application avec un backend interrogeant la base à chaque
chargement.

Chaque script d'extraction a son propre modèle de connexion — pas de
prétention à l'uniformité là où la réalité diverge : `extract_filiation.py`
lit un `target/` dbt compilé, `scan_database.py` et `extract_prefect.py` se
connectent en direct (base SQLAlchemy quelconque ; client Prefect local,
sans authentification par mot de passe), `extract_n8n.py` lit des workflows
déjà versionnés en JSON (l'API n8n live existe mais reste derrière une
authentification jamais résolue ici), et `extract_powerbi.py` (seul cas)
**ne se connecte pas lui-même** à un modèle Power BI — impossible en Python
pur, il transforme un export JSON produit via le MCP `powerbi-modeling`,
accessible uniquement depuis une session Claude Code avec le `.pbix` ouvert.
Voir la section [Power BI](#-power-bi--mesures-et-colonnes-calculées-dax)
plus bas. Le lignage `Table[Colonne]` fonctionne par correspondance de
**nom** avec les nœuds déjà présents, pas par identité physique garantie —
deux systèmes différents partageant un nom de table (ex. `dim_date`) sont
ambigus, résolus arbitrairement (dernier trouvé), comme pour le lignage SQL
clickable existant en cas de token dupliqué.

`scan_database.py` (SQLAlchemy) ne couvre que des bases **relationnelles**
(Postgres/MySQL/SQL Server/SQLite...) — MongoDB (`bv-mongo-logs` sur ce
poste, par exemple) est hors périmètre de l'outil actuel, pas seulement hors
périmètre "projet partagé". `extract_n8n.py` lit la définition **statique**
des workflows (étapes, requêtes), pas leurs exécutions ou statuts en direct
(voir [ROADMAP.md](ROADMAP.md), chantier 4) — `extract_prefect.py`, lui,
lit bien de vraies exécutions passées, mais seulement celles déjà
enregistrées localement (pas de déclenchement, pas d'exécution en direct
depuis l'outil).

Reste à faire : voir [ROADMAP.md](ROADMAP.md).
