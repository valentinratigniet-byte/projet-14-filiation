# Feuille de route

État au 2026-08-21 (15 commits). Quatre chantiers identifiés pour la
reprise, dans l'ordre conseillé : 1) durcir le raccordement aux bases
existant, 2) compléter les informations qu'on en extrait, 3) étendre le
lignage jusqu'à la couche Power BI (nouveau sous-système, plus gros
morceau), 4) connecter l'outil au reste de l'écosystème réel — LLM,
orchestrateur de pipelines, bases multiples (le plus exploratoire, à ne
démarrer qu'après avoir statué sur la frontière avec le projet partagé).

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

- [ ] **Descriptions manquantes générées, jamais imposées** — beaucoup de
      nœuds réels (introspectés depuis un système sans commentaires) portent
      aujourd'hui `"Aucune description renseignée..."`. Un LLM (API Claude,
      ou un Ollama local dédié à ce projet — pas `bv-ollama`) peut proposer
      une description à partir du nom de table/colonne, du type, et du SQL
      environnant. Affichée avec un badge explicite **"Suggestion IA, non
      vérifiée"**, jamais écrite automatiquement dans `demoNodes`/le jeu
      réel — un humain doit la relire et la valider avant qu'elle devienne
      la description officielle (même doctrine que le reste de l'outil :
      pas d'écriture live, une correction passe par une validation
      explicite).
- [ ] **Assistant "posez une question sur vos données"** — un champ de
      question en langage naturel au-dessus du graphe de lignage
      ("d'où vient ce chiffre ?", "qu'est-ce qui casse si je change X ?").
      Contexte envoyé au LLM : uniquement le sous-graphe pertinent (le nœud
      ciblé + ses dépendances/dépendants via `deps`/`usedBy`, déjà calculés
      pour l'analyse d'impact), pas tout `demoNodes`/`realNodes` — reste
      lisible et bon marché en tokens. Répond en langage naturel, ne modifie
      jamais rien.
- [ ] **Explication des échecs de qualité** — sur un check `warn`/`fail`
      (ex. "12 produits sans coût renseigné"), un résumé LLM en une phrase
      de l'impact probable en aval (quels indicateurs métier sont affectés,
      via la fermeture transitive `usedBy` déjà existante) — traduit un fait
      technique en langage compréhensible par un rôle non technique (PDG,
      RH), cohérent avec le filtrage par rôle déjà en place.

### Pipelines (orchestration réelle)

- [ ] Le domaine **Data** du jeu démo (fraîcheur pipelines, succès jobs,
      score qualité) est aujourd'hui entièrement fictif — le remplacer par
      du réel sur le même principe qu'`extract_filiation.py`/
      `scan_database.py` : un nouveau script en lecture seule qui interroge
      un vrai orchestrateur et régénère les nœuds correspondants.
- [ ] Deux pistes déjà présentes sur ce poste : **Prefect** (déjà installé
      dans le venv du [[portfolio-data]] Projet 10, a une API REST pour
      lister flows/runs et leur statut) et **n8n** (API REST
      `/rest/executions`). Prefect est plus simple à isoler pour un premier
      test (2-3 flows factices dans un environnement Prefect dédié à ce
      projet) sans toucher à l'instance partagée.
- [ ] Une fois le pipeline réel branché, les nœuds `pipelines_a_jour`,
      `jobs_reussis`, `score_qualite_donnees`... du domaine Data basculent
      du jeu démo (fictif) vers le jeu réel — cohérent avec la façon dont
      `dbt_ecommerce` alimente déjà le reste du jeu réel.

### Bases de données multiples (fusion réelle)

- [ ] La ROADMAP chantier 1 a déjà identifié la **fusion multi-sources**
      comme un point en attente (`scan_database.py` écrase le rapport
      précédent à chaque lancement plutôt que de fusionner). Ce chantier est
      l'occasion de la construire pour de vrai plutôt que dans l'abstrait :
      scanner successivement deux systèmes réellement différents du poste
      (par ex. un Postgres jetable + un MySQL jetable, sur le modèle du test
      MySQL déjà fait) et vérifier qu'ils apparaissent bien ensemble dans la
      vue Systèmes, avec les collisions d'identifiants de nœuds gérées
      (préfixer par système, déjà noté dans le point correspondant du
      chantier 1).
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
