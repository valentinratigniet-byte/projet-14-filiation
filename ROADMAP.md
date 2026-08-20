# Feuille de route

État au 2026-08-20 (11 commits). Trois chantiers identifiés pour la reprise,
dans l'ordre conseillé : 1) durcir le raccordement aux bases existant,
2) compléter les informations qu'on en extrait, 3) étendre le lignage
jusqu'à la couche Power BI (nouveau sous-système, plus gros morceau).

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
      **Reste à faire** : SQL Server (`pyodbc`) — pas testé, aucune
      instance disponible localement pour le moment.
- [ ] **Grosses bases** : `scan_database.py` scanne aujourd'hui *toutes*
      les tables des schémas ciblés, sans limite. Sur une vraie ERP
      (plusieurs centaines de tables), ce serait long et lourd. Ajouter un
      `--tables` pour cibler une liste précise, ou un mode "aperçu rapide"
      (les N tables les plus volumineuses) avant un scan complet.
- [ ] **Timeouts / retries réseau** — aucun timeout de connexion
      configurable actuellement ; une base distante lente ferait planter le
      scan sans message clair. Ajouter `connect_args` par dialecte + un
      retry avec backoff.
- [ ] **Fusionner plusieurs sources dans un même rapport** — aujourd'hui un
      scan = un système, et relancer le script sur un 2ᵉ système *écrase*
      le premier (`splice()` remplace tout le bloc `realNodes`). Pour un
      vrai audit multi-systèmes (ERP + CRM + entrepôt dans le même
      rapport), il faut faire évoluer `splice()`/le format de données pour
      fusionner au lieu de remplacer (préfixer les ids de nœuds par système
      pour éviter les collisions de clés).
- [ ] **Connexions nommées** — un fichier `connections.yml` gitignored
      listant des alias (nom du système → nom de la variable d'environnement
      à lire), pour ne pas retaper l'URL à chaque audit. Jamais de secret en
      clair dans ce fichier, seulement des noms de variables d'env.
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

- [ ] **Commentaires déclarés en base** (`COMMENT ON TABLE` /
      `COMMENT ON COLUMN`) — beaucoup de DBA documentent directement en base
      plutôt que dans un outil externe. SQLAlchemy les expose déjà :
      `inspector.get_table_comment(table, schema)` et la clé `"comment"` de
      chaque colonne renvoyée par `get_columns()` — actuellement ignorés
      alors que la donnée est disponible sans requête SQL supplémentaire.
- [ ] **Vues** — `insp.get_table_names()` ne renvoie *que* les tables ; les
      vues (`insp.get_view_names()`) sont invisibles aujourd'hui. Une vraie
      base métier a souvent des vues importantes (agrégats, sécurité par
      vue). À ajouter en 2ᵉ passage, avec un badge "vue" distinct
      (`get_pk_constraint`/`get_foreign_keys` ne s'appliquent pas à une vue
      — dégrader proprement plutôt que planter).
- [ ] **Contrainte déclarée vs observée** — `column["nullable"]` (déjà
      renvoyé par `get_columns()`) n'est jamais lu ; seul un test live
      (`check_not_null`) est calculé. Afficher les deux donne un vrai
      signal : une colonne déclarée `NOT NULL` dont le test échoue signale
      une incohérence ; une colonne nullable en théorie mais jamais nulle en
      pratique est candidate à contraindre.
- [ ] **Valeurs par défaut** — `column["default"]`, déjà disponible côté
      SQLAlchemy, jamais affiché.
- [ ] **Index** — `insp.get_indexes()`, utile pour un audit de performance
      (colonnes indexées vs colonnes réellement filtrées/jointes).
- [ ] **Contraintes CHECK** — `insp.get_check_constraints()`.
- [ ] Plus loin, moins prioritaire : vues matérialisées, procédures
      stockées / triggers (logique métier parfois cachée là),
      partitionnement, character set / collation.

## 📊 3. Limites de calcul Power BI — mesures, colonnes calculées, indicateurs

Filiation couvre aujourd'hui la chaîne donnée brute → dbt (SQL) et s'arrête
avant la couche Power BI — alors que le portfolio a déjà deux modèles réels
([Projet 09](../projet-09-dashboard-powerbi),
[Projet 13](../projet-13-entrepot-central-bigquery)) construits via le
**MCP `powerbi-modeling`** déjà installé. C'est la suite naturelle du
lignage colonne-à-colonne déjà construit (sqlglot) : aujourd'hui il
s'arrête à la dernière colonne dbt, alors que dans un vrai rapport cette
colonne alimente encore une mesure DAX avant d'atteindre un visuel.

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

**À faire :**

- [ ] Nouveau script `extract_powerbi.py` qui interroge le MCP
      `powerbi-modeling` (`measure_operations`, `column_operations`,
      `table_operations`, `dax_query_operations`) sur un modèle Power BI
      ouvert (ex. `Dashboard entrepot.pbix`) pour en extraire mesures et
      colonnes calculées, avec leur formule DAX brute.
- [ ] Nœuds `type: "dax-measure"` / `type: "dax-column"` dans le même format
      que les nœuds existants, avec un badge de couleur dédié pour les
      distinguer visuellement des types `metric`/`derived`/`raw` déjà en
      place.
- [ ] **Lignage DAX → colonnes sources** : parser les références
      `Table[Colonne]` dans le texte de chaque formule DAX (regex simple,
      pas un vrai parseur DAX — suffisant pour la majorité des formules)
      pour relier une mesure à ses colonnes, elles-mêmes déjà reliées via
      `upstream` (sqlglot) jusqu'à la donnée brute — complète la chaîne
      bout en bout : base → dbt → Power BI.
- [ ] Sur la fiche d'une mesure/colonne calculée : afficher explicitement
      la distinction colonne calculée vs mesure, avec le rappel
      contexte de ligne/contexte de filtre et coût refresh+stockage vs coût
      requête — objectif pédagogique, cohérent avec le reste de l'outil
      (déjà honnête sur les descriptions manquantes, même logique ici).
- [ ] Détection de motifs à risque, en best-effort sur le texte DAX :
      colonne calculée qui référence une autre colonne calculée (risque de
      cascade au refresh), mesure agrégeant plusieurs tables sans
      `CALCULATE`/`FILTER` (risque de contexte de filtre mal maîtrisé),
      mesures dupliquées entre plusieurs rapports `.pbix` du portfolio.
- [ ] Une fois les données DAX disponibles, les faire apparaître dans les
      4 vues existantes (Fiche / Graphe complet / Dérive / Systèmes) plutôt
      que créer une 5ᵉ vue séparée — le point fort de l'outil est justement
      que tout se navigue au même endroit.

---

**Pourquoi cet ordre** : le point 1 est le plus proche de ce qui tourne déjà
(durcissement d'un code existant). Le point 2 s'enchaîne naturellement
dessus (mêmes fichiers, mêmes appels SQLAlchemy, ajout de champs). Le
point 3 est un nouveau sous-système à part entière (dépend du MCP
`powerbi-modeling` et d'un modèle Power BI ouvert) — à aborder une fois les
deux premiers stabilisés, pas en premier.
