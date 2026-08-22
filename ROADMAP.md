# Feuille de route

État au 2026-08-22 (26 commits). Quatre chantiers identifiés à l'origine :
1) durcir le raccordement aux bases, 2) compléter les informations qu'on en
extrait, 3) étendre le lignage jusqu'à la couche Power BI, 4) connecter
l'outil au reste de l'écosystème réel (LLM, orchestrateur de pipelines,
bases multiples). **Les quatre sont maintenant substantiellement faits** —
il ne reste que des points annexes explicitement marqués `[ ]` ci-dessous
(SQL Server sans instance locale, mesures dupliquées entre plusieurs
`.pbix`, pipelines n8n reportés faute de workflows réels). Détail par
chantier ci-dessous, gardé pour la trace de ce qui a été fait et pourquoi.

## 🔌 1. Raccordement aux bases de données

**En place** : `extract_filiation.py` (projets dbt) et `scan_database.py`
(n'importe quelle base via SQLAlchemy, un système à la fois) — toujours en
lecture seule, identifiants lus uniquement depuis `$DATABASE_URL`.

**Corrigé le 2026-08-20** : `scan_database.py` codait ses requêtes avec des
guillemets doubles en dur (`"schema"."table"`) — valide sur Postgres/SQL
Server, mais invalide par défaut sur MySQL (guillemets doubles = chaîne
littérale, pas un identifiant, sauf `ANSI_QUOTES` activé). Remplacé par
`engine.dialect.identifier_preparer.quote()`, qui choisit le bon quoting
pour le moteur réel. Revérifié contre la base réelle après coup — aucune
régression sur Postgres.

**À faire :**

- [x] **Tester réellement contre un 2ᵉ moteur (MySQL)** — fait le
      2026-08-20 contre un conteneur MySQL 8.4 jetable (schéma à 3 tables,
      FK, clé composite, colonne nullable). Bug réel trouvé et corrigé :
      `insp.get_schema_names()` sur MySQL liste **toutes les bases de
      l'instance** (`mysql`, `performance_schema`, `sys`...), pas seulement
      celle de l'URL de connexion — contrairement à Postgres où un schéma
      est un espace de noms *dans* une base. Sans filtre, le scan par
      défaut remontait 154 tables système au lieu des 3 tables réelles.
      Fix : `default_schemas()` détecte les dialectes MySQL/MariaDB et se
      limite à `engine.url.database` ; le comportement Postgres (tous les
      schémas non-système de la base connectée) est inchangé et re-vérifié
      sans régression contre la vraie base ecommerce (5 tables `raw`, 19
      tables tous schémas). Quoting, FK, clé composite et détection NULL
      confirmés corrects sur MySQL dans la foulée.
      **SQL Server testé le 2026-08-22** : conteneur `mssql/server:2022`
      jetable (`sa`/mot de passe de test, supprimé après coup). Premier
      essai avec le seul pilote ODBC disponible sur ce poste ("SQL Server",
      legacy) : `get_table_names()` échouait avec `HY104 — Valeur de
      précision non valide` — bug de compatibilité connu entre ce pilote et
      les requêtes SQLAlchemy qui bindent des paramètres `NVARCHAR(MAX)`.
      Root cause corrigée, pas contournée : installé `Microsoft ODBC Driver
      18 for SQL Server` (`winget install Microsoft.msodbcsql.18 --source
      winget` — le `--source winget` est nécessaire, la source `msstore`
      échoue sur ce poste à cause de l'interception SSL déjà documentée).
      Une fois le bon pilote installé : tables, vues, colonnes (nullable,
      défaut), index et commentaire de table (extended property
      `MS_Description`) tous corrects. `get_check_constraints()` lève
      `NotImplementedError` sur le dialecte mssql — dégradé proprement via
      `safe()` (chantier 2), confirmé en conditions réelles et pas
      seulement en théorie.
- [x] **Grosses bases** — fait le 2026-08-21. Deux options ajoutées à
      `scan_database.py` : `--tables NOM [NOM ...]` pour cibler une liste
      précise de tables, et `--top N` (aperçu rapide) qui fait d'abord un
      `COUNT(*)` sur *toutes* les tables des schémas ciblés (sans
      introspection de colonnes ni contrôles qualité, donc rapide) puis ne
      scanne en détail que les N tables les plus volumineuses. Les deux
      modes réutilisent le même chemin : un ensemble de tables retenues
      filtre simplement la boucle de scan complet. Une clé étrangère vers
      une table exclue du périmètre est abandonnée silencieusement (`deps`
      vide), même comportement que pour une référence cross-schéma non
      résolue déjà présente dans le code. Testé en direct contre la vraie
      base ecommerce (schéma `raw`, 5 tables) : `--top 2` retient bien
      `order_item` (121 331 lignes) et `orders` (40 400) avant `product` et
      `customer` ; `--tables customer product` scanne exactement ces deux
      tables.
      **Reste à faire** : sur une base à plusieurs centaines de tables, le
      `COUNT(*)` du mode `--top` reste lui-même un scan complet (une requête
      par table) — passer aux statistiques du catalogue
      (`pg_class.reltuples` sur Postgres) si ça devient trop lent en
      pratique (marqué `ponytail:` dans le code).
- [x] **Timeouts / retries réseau** — fait le 2026-08-22. `make_engine()`
      passe un `connect_args` propre à chaque dialecte (`connect_timeout` sur
      Postgres/MySQL/MariaDB, `timeout` sur SQL Server/pyodbc — chaque pilote
      DBAPI a sa propre convention de nom, aucune ne s'applique aux autres ;
      SQLite est local, pas de réseau à borner). `connect_with_retry()`
      teste la connexion avant de lancer le scan (3 tentatives par défaut,
      backoff exponentiel `1.5^tentative`), lève un message clair après
      épuisement plutôt que l'exception SQLAlchemy brute. Deux nouvelles
      options : `--connect-timeout` (10s par défaut, 0 pour désactiver) et
      `--retries`. Testé en direct contre un port fermé
      (`postgresql+psycopg2://…@127.0.0.1:59999/nodb`, `--connect-timeout 2
      --retries 2`) : échoue proprement en ~3,5s avec le message attendu au
      lieu de pendre indéfiniment ; re-testé contre la vraie base ecommerce
      (Docker relancé) pour confirmer l'absence de régression sur le chemin
      normal.
- [x] **Fusionner plusieurs sources dans un même rapport** — fait le
      2026-08-21. `scan_database.py --merge` relit le `realNodes` déjà
      présent dans `index.html`, y fusionne les nœuds fraîchement scannés
      (`dict.update`) au lieu de tout remplacer. Les ids de nœuds sont
      désormais préfixés par système (`tbl_{système}_{table}`, ex.
      `tbl_postgresql_ecommerce_customer`) pour éviter toute collision entre
      deux systèmes ayant une table de même nom — sans `--merge`, le
      comportement par défaut (un scan remplace tout) est inchangé. La vue
      Systèmes n'a demandé aucun changement : elle groupe déjà dynamiquement
      par `source.system`.
      **Bug réel trouvé en testant** : en scannant un système minimal (sans
      jamais avoir lancé `extract_filiation.py`), le sélecteur de jeu de
      données plantait au clic sur "Projet réel" — `DATASET_ROOT.real`
      pointe en dur vers `"fct_sales"` (un nœud du jeu dbt), et pour les
      rôles dont `canSeeNode` ignore son argument (admin/it/exploitation,
      tous `() => true`), l'absence du nœud passait inaperçue : la racine
      choisie n'existait pas dans `nodes`, et `renderBreadcrumb()` plantait
      sur `nodes[id].short`. Corrigé en vérifiant l'existence du nœud avant
      son accessibilité (`nodes[preferredRoot] && canSeeNode(...)` plutôt
      que `canSeeNode(nodes[preferredRoot])` seul). Testé en direct : fusion
      réelle de la base ecommerce (2 tables) avec un Postgres jetable
      (1 table) — 3 nœuds, 2 cartes dans la vue Systèmes, navigation et
      changement de rôle sans erreur (jsdom).
- [x] **Connexions nommées** — fait le 2026-08-22. `connections.yml` à la
      racine du repo (ajouté à `.gitignore`, jamais commité), un
      `connections.example.yml` versionné documente le format : alias → nom
      de la variable d'environnement à lire, jamais un secret en clair.
      `--conn ALIAS` résout l'URL via `$<variable>` ; priorité `--url` >
      `--conn` > `$DATABASE_URL`, inchangée si aucun alias n'est utilisé.
      Testé en direct : `--conn ecommerce_test` avec un `connections.yml`
      temporaire pointant vers `$SCAN_TEST_DB_URL`, contre la vraie base
      ecommerce, scanné avec succès (`--tables customer product`) ; fichier
      de test supprimé après coup, jamais commité.
- [ ] *(Plus tard, chantier à part)* **Portail hébergé multi-ERP** — déjà
      écarté sciemment pour l'instant (voir README). Modèle de risque
      différent : coffre-fort à secrets, contrôle d'accès réseau, plus une
      vraie application avec backend. Ne pas commencer avant que la version
      "script local" soit mature.

## 🗂️ 2. Complétude des informations de base

`scan_database.py` lit aujourd'hui : tables, colonnes + types, clés
primaires/étrangères réelles, volumétrie, et calcule en direct deux
contrôles (non-nullité, unicité sur clé simple). Plusieurs informations déjà
disponibles via SQLAlchemy/le SGBD ne sont **pas encore** exploitées :

- [x] **Commentaires déclarés en base** — fait le 2026-08-22.
      `insp.get_table_comment(table, schema)` (via `safe()`, `NotImplementedError`
      sur SQLite par exemple) devient la `description` du nœud quand il existe
      (sinon le texte générique inchangé) ; la clé `"comment"` de chaque
      colonne renvoyée par `get_columns()` s'affiche sous le nom de colonne.
- [x] **Vues** — fait le 2026-08-22. `insp.get_view_names(schema)` fusionné
      au même traitement que les tables (mêmes ids, mêmes déps), badge
      "Vue" distinct dans l'en-tête de fiche. `get_pk_constraint`/
      `get_foreign_keys`/`get_table_comment`/`get_indexes`/
      `get_check_constraints` passent tous par `safe()` (dégradent en liste
      vide/`None` plutôt que planter) — nécessaire pour les vues mais
      généralisé à toute introspection non garantie par le dialecte.
- [x] **Contrainte déclarée vs observée** — fait le 2026-08-22.
      `column["nullable"]` affiché en badge "NOT NULL" à côté du nom de
      colonne. Signal de cohérence ajouté au passage : si une colonne
      déclarée `NOT NULL` échoue quand même au test live `check_not_null`,
      une note "— pourtant déclarée NOT NULL en base" s'ajoute au contrôle
      qualité existant (pas un nouveau test, un enrichissement du même).
- [x] **Valeurs par défaut** — fait le 2026-08-22. `column["default"]`
      affiché sous le nom de colonne (avec le commentaire, si présent).
- [x] **Index** — fait le 2026-08-22. `insp.get_indexes()` (via `safe()`)
      → section "Index & contraintes" sur la fiche (nom, colonnes,
      unique/non-unique).
- [x] **Contraintes CHECK** — fait le 2026-08-22. `insp.get_check_constraints()`
      (via `safe()`) → même section, texte SQL brut de la contrainte.

      **Vérifié** : DB SQLite jetable (`test_scan_chantier2.py`, stdlib
      `sqlite3` + SQLAlchemy, aucun conteneur partagé touché) couvrant les 6
      points en une fois — table + vue, colonne `NOT NULL` vs nullable,
      valeur par défaut, contrainte CHECK, index nommé, dégradation propre
      de `get_table_comment` (non supporté sur SQLite). Rendu HTML vérifié
      en jsdom (badge "Vue", badge "NOT NULL", ligne commentaire/défaut,
      sous-sections Index/CHECK, pas de section fantôme quand vide).
      Re-testé contre la vraie base ecommerce (Docker relancé puis arrêté) :
      aucune régression ; cette base n'a ni index/CHECK/commentaires/valeurs
      par défaut déclarés sur `raw.customer`/`raw.product` (landing zone non
      contrainte, cohérent avec ce qui avait déjà été constaté au chantier 1
      "relations inférées" — pas de vraie contrainte FK non plus).
- [ ] Plus loin, moins prioritaire : vues matérialisées, procédures
      stockées / triggers (logique métier parfois cachée là),
      partitionnement, character set / collation.

## 📊 3. Limites de calcul Power BI — mesures, colonnes calculées, indicateurs

**Fait le 2026-08-22** : Filiation couvrait la chaîne donnée brute → dbt
(SQL) et s'arrêtait avant la couche Power BI — alors que le portfolio a deux
modèles réels ([Projet 09](../projet-09-dashboard-powerbi),
[Projet 13](../projet-13-entrepot-central-bigquery)) construits via le
**MCP `powerbi-modeling`** déjà installé. `extract_powerbi.py` complète
maintenant cette chaîne (17 mesures DAX du Projet 13 extraites et reliées) —
détail dans "À faire" ci-dessous, resté en place pour la trace.

**Ce que "limites de calcul" veut dire concrètement, à documenter et à
détecter dans l'outil :**

- **Colonne calculée (DAX)** : évaluée ligne par ligne **au refresh**,
  stockée physiquement dans le modèle (coûte de la RAM), figée jusqu'au
  refresh suivant, en **contexte de ligne**.
- **Mesure (DAX)** : évaluée **à la requête** (quand un visuel se dessine),
  jamais stockée, toujours à jour par rapport au filtre courant, en
  **contexte de filtre** — nécessite `CALCULATE()` pour changer de
  contexte, la source n°1 d'erreurs DAX.
- Une mesure et une colonne calculée qui font "le même calcul" n'ont donc ni
  le même coût, ni la même fraîcheur, ni le même comportement face à un
  filtre — un mauvais choix entre les deux a un impact perf/fraîcheur réel,
  pas seulement stylistique.

**Fait le 2026-08-22 :**

- [x] **`extract_powerbi.py`**, avec une différence d'architecture assumée
      par rapport aux deux autres scripts : il **ne se connecte pas
      lui-même** à un modèle Power BI (impossible en Python pur — seul le
      MCP `powerbi-modeling`, Tabular Object Model via Analysis Services,
      sait parler à un modèle live, et il n'est accessible que depuis une
      session Claude Code avec le `.pbix` ouvert). Le script consomme un
      export JSON (`--from-json`, schéma documenté dans
      `powerbi_export.example.json`) produit en amont via
      `connection_operations` (`ListLocalInstances`+`Connect`) puis
      `table_operations`/`measure_operations`/`column_operations`
      (`List`+`Get` — `List` ne renvoie pas l'expression DAX, il faut `Get`).
      Toujours additif (contrairement à `scan_database.py` sans `--merge`) :
      fusionne systématiquement avec les nœuds réels déjà présents, jamais
      de remplacement — un modèle Power BI vient s'ajouter à une extraction
      dbt/base existante, il ne la remplace jamais.
- [x] Nœuds `type: "dax-measure"` / `type: "dax-column"`, couleur dédiée
      (`--dax`, un bleu, absent de la palette metric/derived/raw existante)
      déclinée sur les 5 endroits où les autres types ont une couleur
      (pastille sidebar, badge, token cliquable, puce colonne-à-colonne,
      pastille système). `buildSidebar()` et `groupBySystem()` (vue
      Systèmes) codaient en dur la liste des types (`["metric", "derived",
      "raw"]` / `type === "raw"`) — étendus, sans quoi `buildSidebar`
      plantait (`push` sur `undefined`) au premier nœud DAX rencontré.
- [x] **Lignage DAX → sources, cliquable** : `daxFragment()` (nouvelle
      fonction JS, même famille que `sqlFragment`/`realSqlFragment`
      existants) rend cliquable `Table[Colonne]` (→ table dbt/base
      correspondante, par nom) et `[Mesure]` sans préfixe (→ autre mesure du
      même modèle) dans le texte affiché de la formule — pas seulement dans
      `deps` (utilisé par le graphe/l'analyse d'impact). Le lignage
      colonne-à-colonne sqlglot existant prend le relais en amont : cliquer
      une table Power BI mène à une fiche dbt qui a déjà ses propres
      colonnes reliées à la donnée brute. Résolution par **correspondance de
      nom**, pas une garantie d'identité physique — deux systèmes fusionnés
      avec une table de même nom (ex. `dim_date`, présent à la fois dans
      `dbt_ecommerce` et `bv-postgres-dbtdev`) sont ambigus, résolus
      arbitrairement (dernier trouvé dans l'itération) ; documenté dans le
      README plutôt que masqué.
- [x] Carte pédagogique sur la fiche de toute mesure/colonne calculée :
      distinction explicite contexte de ligne (colonne, stockée, figée au
      refresh) vs contexte de filtre (mesure, recalculée à la requête,
      `CALCULATE()` pour en changer). Le texte DAX brut partage le bloc
      "Détails techniques" déjà utilisé pour le SQL réel — libellé de
      section conditionnel ("Formule DAX" au lieu de "Définition SQL") via
      un nouveau `node.sqlKind === "dax"`, réutilise le mécanisme de rendu
      existant (`sqlFragment` neutralisé sans effet sur du texte DAX,
      confirmé plutôt que supposé) sans dupliquer la section "Détails
      techniques".
- [x] Détection de motifs à risque, réutilisant le mécanisme `quality`
      existant (pastilles ok/warn + bouton IA "Expliquer l'impact" déjà
      construit au chantier 4 — aucun code neuf nécessaire pour ça) :
      mesure référençant plusieurs tables sans `CALCULATE`/`CALCULATETABLE`/
      `FILTER`/`ALL`/`ALLEXCEPT`/`ALLSELECTED`/`REMOVEFILTERS` (regex sur
      les noms de fonction) ; colonne calculée référençant une autre colonne
      calculée. Sur le modèle réel testé (17 mesures, 0 colonne calculée),
      aucune n'est signalée risquée — modèle compétemment construit, pas un
      artefact du détecteur (vérifié séparément avec un cas volontairement
      risqué dans `test_extract_powerbi.py`).
- [x] Intégré dans les 4 vues existantes, pas de 5ᵉ vue : les nœuds DAX sont
      des `realNodes` comme les autres, donc Graphe complet/Dérive/Systèmes
      les héritent sans code spécifique — seule la vue Systèmes a demandé un
      changement explicite (`type === "raw"` élargi) puisqu'elle filtrait
      par type. Rôle **PDG** étendu (`canSeeNode`) pour voir les mesures DAX
      en plus des `metric` — c'était le sens de la remarque déjà notée
      "PDG vide sur le jeu réel, ce projet dbt n'a pas encore de couche
      KPI" : les mesures Power BI comblent exactement ce vide.

**Vérifié** : `scripts/test_extract_powerbi.py` (parsing DAX, détection de
risque, construction de nœuds — sans modèle live, `test_scan_database.py` du
chantier 2 avait établi ce pattern). Rendu HTML vérifié en jsdom (badge,
carte pédagogique, libellé "Formule DAX", références cliquables — y compris
un clic réel qui navigue vers `fct_sales` —, PDG voit les mesures, carte
Power BI dans la vue Systèmes, pas de régression sur Graphe complet).
**Exécuté pour de vrai** contre `Dashboard entrepot.pbix` (Projet 13, Power
BI Desktop ouvert et fermé dans la foulée) : 17 mesures extraites avec leur
vraie formule DAX, lignage résolu correctement y compris mesure-à-mesure
(`Panier moyen` → `CA` + `Nb commandes`) et mesure-à-table à travers
plusieurs niveaux de `TOTALYTD`/`SAMEPERIODLASTYEAR`/`CALCULATE` ; 0 colonne
calculée (confirmé via `columnType` sur les 30 colonnes du modèle — les 17
mesures portent toute la logique, aucune colonne DAX). Fusionné dans
`index.html` (56 nœuds réels au total). ROADMAP chantier 3 désormais bouclé
en entier (voir mesures dupliquées ci-dessous, fait le 2026-08-22).

- [x] **Mesures dupliquées entre plusieurs rapports `.pbix`** — fait le
      2026-08-22. Nouveau script `scripts/find_duplicate_powerbi_measures.py`,
      séparé de `extract_powerbi.py` (qui reste un export → un ensemble de
      nœuds) : prend N `--from-json` (un par modèle déjà extrait), détecte
      deux types de duplication indépendamment — **même nom** (across
      modèles) et **même formule** (texte DAX normalisé, espaces/casse
      ignorés) — et **annote** (n'ajoute aucun nœud) les nœuds `dax-measure`
      déjà présents avec un contrôle qualité "Mesure dupliquée entre
      rapports", réutilisant tel quel le mécanisme pastille/bouton IA
      "Expliquer l'impact" du chantier 4 (zéro code HTML/JS neuf). `--apply`
      pour écrire, sans (dry-run) pour juste afficher le rapport ; idempotent
      (ne réinjecte pas une annotation déjà posée par un run précédent —
      vérifié par un test dédié).
      **Exécuté pour de vrai** : Power BI Desktop ouvert une seconde fois sur
      `dashboard-ventes.pbix` (Projet 09, séquentiellement après Projet 13 —
      pas besoin des deux instances simultanément, contrairement à ce qui
      était supposé). 17 mesures extraites, mêmes 17 noms que Projet 13
      (confirme "hérité du même socle"). Comparaison réelle : **17
      duplications de nom, 15 duplications de formule** — 2 mesures
      (`CA moyenne 3M`, `Rang produit`) ont le même nom mais une formule
      *différente* entre les deux rapports (Projet 09 a une version plus
      robuste de la moyenne glissante avec gestion du blanc), signal
      réellement utile qu'une simple comparaison de nom aurait raté.
      **Limite assumée** (regex, pas un vrai parseur DAX) : `Rang produit`
      a la même logique dans les deux rapports mais un espacement différent
      autour d'un argument positionnel vide (`, ,` vs `,,`) que la
      normalisation par espaces ne résorbe pas — resterait un faux négatif
      sur la détection "même formule" (rattrapé par la détection "même nom").

## 🔗 4. Connexion à l'écosystème réel (LLM, pipelines, bases multiples)

Les trois premiers chantiers durcissent l'outil sur ce qu'il sait déjà faire
(une base, un projet dbt, un modèle Power BI). Celui-ci le connecte à ce qui
tourne réellement autour — remplacer par du vrai ce qui est aujourd'hui
fictif ou isolé, sans jamais sortir de la doctrine déjà actée : lecture
seule, jamais d'écriture automatique, une suggestion reste une suggestion
tant qu'un humain ne l'a pas validée. Voir
[[governance-read-only-preference]].

**⚠️ Frontière à respecter avant de commencer** : ce poste fait tourner des
conteneurs qui appartiennent au [[projet-baptiste-valentin]] (`bv-ollama`,
`bv-n8n`, `bv-mysql-crm`, `bv-postgres-dbtdev`, `bv-mongo-logs`) — un projet
binôme avec Baptiste, pas un bac à sable pour ce projet-ci. Ne pas les
réutiliser pour Filiation sans en parler à Valentin d'abord (accès
concurrent, données qui ne sont pas seulement les siennes). Le réflexe déjà
validé sur ce projet (voir 12ᵉ commit) est de préférer un conteneur jetable
dédié — `docker run` isolé, nettoyé après le test — exactement comme pour le
test MySQL de la ROADMAP chantier 1. Si un vrai besoin de connexion durable
à `bv-*` se présente, c'est une décision à prendre avec Valentin, pas un
raccourci technique.

### LLM

- [x] **Descriptions manquantes générées, jamais imposées** — fait le
      2026-08-22. `scripts/suggest_descriptions.py` interroge `bv-ollama`
      (local, `llama3.2:3b`, autorisé par Valentin — voir le 17e-19e commit
      pour la même autorisation côté bases) en HTTP simple (`urllib`
      stdlib, aucune dépendance ajoutée) pour chaque nœud dont la
      description est encore le texte générique
      (`"Aucune description renseignée..."` / `"aucune documentation
      associée"`). Le résultat est écrit dans un champ **séparé**
      `aiSuggestion`, jamais dans `description` — affiché dans la fiche
      avec un badge **"🤖 Suggestion IA — non vérifiée"** en encadré
      pointillé, visuellement distinct du texte officiel. Idempotent (ne
      retraite pas un nœud déjà suggéré sauf `--force`) et best-effort (un
      nœud sur lequel Ollama échoue est simplement laissé de côté, le
      script continue). Aucune écriture automatique de la doctrine
      officielle — un humain reste seul juge de ce qui devient la vraie
      description (dans dbt, dans un `COMMENT ON`...).
- [x] **Assistant "posez une question sur vos données"** — fait le
      2026-08-22. Nouvel onglet **Assistant** dans `index.html` (sélecteur
      d'élément + question libre + réponse). Appel `fetch` client, direct
      depuis la page vers `bv-ollama` (`llama3.2:3b`, `POST
      http://localhost:11434/api/generate`) — aucune dépendance, aucun
      script serveur, cohérent avec le principe "page statique, sans
      backend" déjà posé pour ce projet. Contexte envoyé : uniquement le
      sous-graphe pertinent (le nœud choisi + ses dépendances/dépendants
      directs via `deps`/`usedBy`, réutilisés tels quels), jamais tout
      `demoNodes`/`realNodes`. Onglet gouverné par le même mécanisme de
      rôles que Graphe/Dérive/Systèmes (`sections.assistant`) — visible pour
      Administrateur/Informatique/Exploitation, masqué pour PDG/RH dans
      cette première version. Dégrade proprement si `bv-ollama` ne tourne
      pas ou est injoignable (message d'erreur explicite plutôt qu'un plantage
      silencieux). Vérifié en jsdom (onglet visible/masqué par rôle, sélecteur
      peuplé, chemin d'erreur réseau affiché correctement, bouton
      réactivé après échec) — `bv-ollama` n'était pas démarré au moment du
      test, pas encore vérifié avec une vraie réponse du modèle.
      **Reste à faire** : vérifier une vraie réponse avec `bv-ollama` lancé ;
      CORS non testé (Ollama filtre l'`Origin` par défaut — si la page est
      ouverte en `file://`, `OLLAMA_ORIGINS` pourrait devoir être élargi côté
      conteneur ; à vérifier au premier essai réel).
- [x] **Explication des échecs de qualité** — fait le 2026-08-22. Bouton
      "🤖 Expliquer l'impact" affiché à côté de chaque pastille de contrôle
      `warn`/`fail` (jamais sur un `ok`, rien à expliquer) — présent à la
      fois sur les indicateurs/calculs du jeu démo (`node.quality`) et sur
      les tests dbt par colonne du jeu réel (`col.tests`). Au clic, appelle
      `bv-ollama` (même endpoint que l'onglet Assistant) avec pour contexte
      le libellé/statut/note du contrôle + la fermeture transitive `usedBy`
      déjà calculée pour l'analyse d'impact (`downstreamClosure`, filtrée
      par `canSeeNode` pour ne jamais nommer à un rôle un élément qu'il ne
      peut pas voir), et affiche une explication en une phrase dans un
      encadré "non vérifiée" identique à celui des suggestions de
      description. **Volontairement pas gouverné par `sections.assistant`**
      (contrairement à l'onglet Assistant) : PDG et RH y ont accès sur les
      éléments qu'ils voient déjà, c'était l'intention explicite de ce point
      de la ROADMAP ("traduit un fait technique en langage compréhensible
      par un rôle non technique"). Corrigé au passage : `columnsSection`
      n'affichait jamais la `note` d'un test de colonne dans sa pastille
      (contrairement à `node.quality`) — incohérence pré-existante, alignée
      sur le même format `label — note`. Vérifié en jsdom (bouton présent
      uniquement sur les checks non-ok, jeu démo ET jeu réel, chemin
      d'erreur réseau simulé). `bv-ollama` non démarré pendant cette
      session — même limite que l'onglet Assistant, pas encore vérifié avec
      une vraie réponse.

### Pipelines (orchestration réelle)

**Tenté le 2026-08-22, reporté une première fois** : `bv-n8n` répond
(`/healthz` → 200) mais son API (`/rest/executions`, `/api/v1/*`) exige une
authentification — session via `/rest/login` (email+mot de passe) ou clé API
générée à la main dans Settings → API. Les deux passent par la soumission
d'un identifiant dans une commande, ce que le classificateur auto-mode de
Claude Code refuse automatiquement (même blocage que pour les connexions
base de données, voir chantier 1/17e commit). Surtout : **`bv-n8n` n'avait
encore aucun workflow ni exécution réelle** à ce moment-là.

- [x] **Fait le 2026-08-22, plus tard le même jour** — entre-temps, le
      projet partagé a livré son Sprint 5 (Hub n8n) : `bv-n8n` porte
      maintenant **5 workflows réels, versionnés en JSON** dans
      `projet-baptiste-valentin/n8n/workflows/*.json` (dette technique du
      projet partagé déjà résolue de leur côté : les workflows étaient créés
      à la main via l'API, jamais versionnés — voir leur `docs/N8N.md`).
      **Contournement du blocage d'authentification, pas une résolution** :
      plutôt que d'interroger l'API n8n live (toujours bloquée par
      l'authentification), `scripts/extract_n8n.py` lit directement ces
      fichiers JSON déjà versionnés — lecture de fichiers, aucune connexion,
      aucun identifiant, aucun classificateur à contourner. Un nœud
      `type: "pipeline"` par workflow : déclencheur (chemin webhook), étapes
      dans l'ordre de l'export (`pipelineSteps`, nouvelle section "Étapes du
      pipeline" sur la fiche), lignage textuel best-effort vers les tables
      dbt/base déjà présentes (regex `schema.table` sur le texte des
      requêtes Postgres des nœuds `n8n-nodes-base.postgres`, schémas connus
      du projet partagé — `public_marts`/`raw`/`erp_migre`/`marts`/
      `staging` — pour ne pas confondre un alias de table avec un vrai
      schéma). Contrôle qualité réel tiré de la documentation du projet
      partagé ("règle d'or : tous les accès Postgres se font sur
      `public_marts`, jamais sur `raw`/`erp_migre`") : un workflow qui
      référence directement `raw`/`erp_migre` est signalé — aucun des 5
      workflows réels ne l'est (règle respectée), vérifié avec un cas
      volontaire non conforme dans `test_extract_n8n.py`. Toujours additif
      comme `extract_powerbi.py`. Nouveau type `dax`-like `pipeline` (couleur
      dédiée `--pipeline`, rose) décliné aux mêmes 5 endroits CSS que
      `dax-measure`/`dax-column` ; carte "n8n — bv-dataplatform" dans la vue
      Systèmes. **Bug latent trouvé en étendant** : les tokens de couleur
      `--dax`/`--dax-soft` du chantier 3 manquaient dans le bloc
      `:root[data-theme="dark"]` (présents seulement dans le bloc
      `@media (prefers-color-scheme: dark)`, indentation différente —
      l'édit précédent ne les avait remplacés que dans un seul des deux
      blocs) : un thème sombre choisi explicitement (pas juste la préférence
      système) aurait affiché un badge Power BI sans couleur. Corrigé au
      passage pour `--dax` ET dès le départ pour `--pipeline`. Fusionné dans
      `index.html` (61 nœuds réels). Reste ouvert, mineur : lecture des
      workflows en JSON statique, pas d'exécutions/statuts live (nécessiterait
      l'API authentifiée, toujours bloquée) — cohérent avec le principe déjà
      acté pour Power BI/dbt : Filiation documente la *structure* réelle, pas
      un flux temps réel.
- [ ] Le domaine **Data** du jeu démo (fraîcheur pipelines, succès jobs,
      score qualité) reste entièrement fictif — pourrait être remplacé par
      du réel sur le même principe (nœuds `pipeline` du jeu réel), pas fait
      pour l'instant : le jeu démo sert de vitrine pédagogique volontairement
      indépendante du jeu réel.
- [ ] Deux pistes déjà présentes sur ce poste : **Prefect** (déjà installé
      dans le venv du [[portfolio-data]] Projet 10, a une API REST pour
      lister flows/runs et leur statut, pas d'auth par mot de passe) et
      **n8n** (API REST, mais authentification requise — voir ci-dessus).
      Prefect est plus simple à isoler pour un premier test (2-3 flows
      factices dans un environnement Prefect dédié à ce projet) sans
      toucher à l'instance partagée ni au problème d'authentification.
- [ ] Une fois le pipeline réel branché, les nœuds `pipelines_a_jour`,
      `jobs_reussis`, `score_qualite_donnees`... du domaine Data basculent
      du jeu démo (fictif) vers le jeu réel — cohérent avec la façon dont
      `dbt_ecommerce` alimente déjà le reste du jeu réel.

### Bases de données multiples (fusion réelle)

- [x] **Fusion multi-sources construite ET testée en réel** — fait le
      2026-08-21/22. `scan_database.py --merge` (voir chantier 1) validé
      d'abord contre un conteneur jetable, puis en conditions réelles :
      accord donné par Valentin (chef de projet) et Baptiste pour utiliser
      les systèmes du projet partagé `bv-dataplatform` comme premier vrai
      test multi-systèmes. Fusion du jeu réel `dbt_ecommerce` (13 nœuds)
      avec `bv-postgres-dbtdev` (23 tables, schémas `erp_migre`/
      `public_marts`/`raw`) et `bv-mysql-crm` (3 tables, schéma `crm`) → 39
      nœuds, 3 systèmes distincts dans la vue Systèmes, aucune collision
      d'id. Seule la structure (tables/colonnes/types/volumétrie/checks
      qualité) est capturée — `scan_database.py` n'extrait jamais de valeur
      de donnée. Commité et poussé sur le repo public avec l'accord
      explicite des deux (voir [[projet-baptiste-valentin]]).
- [ ] Documenter clairement, une fois ce chantier commencé, la limite de
      `scan_database.py` : basé sur SQLAlchemy, donc uniquement des bases
      relationnelles (Postgres/MySQL/SQL Server/SQLite...) — pas MongoDB
      (`bv-mongo-logs` en est un exemple présent sur ce poste, mais hors
      périmètre de l'outil actuel, pas seulement hors périmètre "projet
      partagé").

---

**Pourquoi cet ordre** : le point 1 est le plus proche de ce qui tourne déjà
(durcissement d'un code existant). Le point 2 s'enchaîne naturellement
dessus (mêmes fichiers, mêmes appels SQLAlchemy, ajout de champs). Le
point 3 est un nouveau sous-système à part entière (dépend du MCP
`powerbi-modeling` et d'un modèle Power BI ouvert) — à aborder une fois les
deux premiers stabilisés, pas en premier. Le point 4 vient en dernier
volontairement : c'est le seul qui touche des ressources hors de ce projet
(LLM, orchestrateur, bases multiples) et donc le seul qui demande de statuer
d'abord sur la frontière avec le projet partagé — pas un chantier à lancer
à la légère un soir.
