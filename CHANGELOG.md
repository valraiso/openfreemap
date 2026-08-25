# Changelog

Toutes les modifications notables de ce projet sont documentées dans ce fichier.

Le format s'appuie sur [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/).

## [Non publié]

### Added

- 2026-08-24 — Option `SINGLE_PLANET=true` (`config/.env` → `single_planet` dans `config.json`) : un seul run planet conservé sur disque, celui réellement servi (version `deployed`), avec repli sur le run local le plus récent tant que la version `deployed` n'est pas téléchargée (on ne supprime jamais le dernier run servable). `monaco` garde le comportement upstream. Le run `latest` du planet n'est plus téléchargé dans ce mode (il serait supprimé aussitôt, donc re-téléchargé chaque nuit). Documentation : section « Keeping a single planet run » de `docs/self_hosting.md`, décision D004.

- 2026-08-24 — Documentation : section « Checking whether the OFM sync ran and succeeded » dans `docs/self_hosting.md` (+ pointeur depuis le README) — comment distinguer une MAJ OFM *tentée* d'une MAJ *réussie* : journal cron (`journalctl -u cron | grep 'http_host.py sync'`, seule trace qui survit aux redeploys), lecture de `logs/http_host_sync.log` (tableau des lignes typiques : `file exists, skipping download`, `not enough disk space`, `Running auto clean btrfs` = quelque chose a changé), comparaison version locale / `deployed_versions/*.txt` / version servie / dernières versions du bucket, limites du healthcheck (il compare servi vs deployed, pas vs dernier dispo), et commande de sync manuel hors fenêtre nocturne (à lancer en tant qu'`ofm` à cause du lockfile `/tmp`).

- 2026-08-21 — Stats d'origines et blocage d'origines abusives :
  - **Access log nginx activé, toujours sans adresse IP** : lignes JSON dans `/data/ofm/http_host/logs_nginx/le-access.jsonl` (time, status, bytes, dataset, flag blocked, Origin, Referer, user-agent), rotation logrotate quotidienne avec 14 jours de rétention (`/etc/logrotate.d/ofm_http_host`).
  - **Stats** : commande `http_host.py stats [--days N]` (table par origine : requêtes / GB / bloquées / statuts, + volumes par dataset) et rapport Slack hebdomadaire `http_host.py stats-report` via le cron `/etc/cron.d/ofm_stats_report` (lundi 08:05, installé seulement si `SLACK_BOT_TOKEN`/`SLACK_CHANNEL` sont renseignés).
  - **Blocage** : commandes `http_host.py block/unblock/blocked <domaine>` — un domaine bloqué (sous-domaines inclus) reçoit un 403 dès que le host de son `Origin` **ou** `Referer` matche. Liste persistante dans `/data/ofm/http_host/config/blocked_origins.txt` (survit aux redeploys), map nginx générée dans `/data/nginx/config/ofm_blocked.conf` à chaque deploy/sync/block/unblock, reload nginx automatique après `nginx -t`.
  - Documentation : section « Origin statistics & abusive-origin blocking » de `docs/self_hosting.md`, décision D003, requêtes de test dans `examples/requests.http`.

- 2026-08-17 — Healthcheck avec alerte Slack : nouvelle commande `http_host.py healthcheck` + cron `/etc/cron.d/ofm_healthcheck` (toutes les 5 min, installé au deploy seulement si `SLACK_BOT_TOKEN`/`SLACK_CHANNEL` sont renseignés dans `config/.env`). Vérifie les TileJSON `/planet` et `/monaco` (HTTP 200), une tuile d'exemple, la cohérence version servie / version deployed (sync bloqué), et l'espace disque libre (seuil `HEALTHCHECK_MIN_FREE_GB`, défaut 300 GB). Anti-spam : un message par changement d'état (panne/rétablissement) + rappel quotidien tant que la panne dure (état dans `healthcheck_state.json`). Documentation : section « Healthcheck » de `docs/self_hosting.md`.

- 2026-08-17 — Home de démo : `https://DOMAIN/` sert une carte MapLibre sur le style `winter` (page `modules/http_host/demo/index.html`, déployée dans `assets/demo/`), protégée par basic auth (`DEMO_AUTH_USER`/`DEMO_AUTH_PASS` dans `config/.env`, htpasswd généré au deploy dans `/data/nginx/htpasswd_demo`). Remplace la redirection 302 upstream vers openfreemap.org. Seul `/` est protégé, les tuiles/styles/assets restent publics. Requêtes de test dans `examples/requests.http`.

- 2026-07-13 — Service d'assets custom déposés par des process externes (le repo ne les produit ni ne les déploie) :
  - **Datasets de tuiles additionnels** : arborescences `{z}/{x}/{y}.{ext}` déposées dans `/data/ofm/http_host/tiles/{dataset}/`, servies sur `/tiles/{dataset}/{z}/{x}/{y}.{ext}` (cache 10 ans, datasets immuables versionnés par leur nom). Vecteur (`.pbf` pré-gzippé, servi avec `Content-Encoding: gzip`, tuile manquante → 200 vide) et raster (`.webp`/`.png`/`.jpg`/`.jpeg`/`.avif`, servis tels quels sans gzip, tuile manquante → 404). TileJSON optionnel servi sur `/tiles/{dataset}` si un `tilejson.json` est déposé à la racine du dataset (placeholder `__TILEJSON_DOMAIN__` substitué par nginx).
  - **Styles MapLibre custom** : fichiers `{name}.json` déposés dans `assets/styles/custom/`, servis sur `/styles/{name}` avec priorité sur les styles OFM du même nom. Le dossier n'est jamais touché par la synchro OFM.
  - Les locations nginx sont génériques (regex) : ajouter un dataset ou un style ne nécessite aucun reload nginx. Les dossiers `tiles/` et `assets/styles/custom/` sont créés au deploy (`prepare_http_host`) sans jamais effacer leur contenu.
  - Documentation : section « Custom assets » dans `docs/self_hosting.md` (protocole de dépôt, sprites custom inclus) et requêtes de test dans `examples/requests.http`.

### Changed

- 2026-08-24 — Besoin d'espace disque avant téléchargement estimé au réel : `taille du .gz + taille de l'image locale × 1,05` (le pic est le `.gz` et l'image décompressée qui coexistent le temps de `unpigz`), au lieu du `3 × .gz` upstream — 270 Go au lieu de 294 Go pour le planet d'août 2026, ce qui fait la différence entre une MAJ possible et une MAJ sautée sur un volume de 500 Go. Repli sur `3 × .gz` en l'absence d'image locale de référence (serveur neuf). Le log de sync affiche désormais le besoin et l'espace libre en Go en plus des octets.

- 2026-08-24 — Healthcheck : la vérification disque compare l'espace libre au besoin réel de la **prochaine** MAJ planet (calculé sur le `.gz` distant le plus récent) au lieu du seuil fixe `HEALTHCHECK_MIN_FREE_GB`, qui devient le repli utilisé quand le bucket btrfs est injoignable. L'alerte reste ainsi pertinente à mesure que le planet grossit, sans réglage manuel.

- 2026-08-21 — Stats d'origines : les requêtes sans `Origin` ni `Referer` sont désormais détaillées par user-agent (`(ua) curl/8.6.0`, tronqué à 40 caractères) au lieu d'être agrégées sous `(none)` ; `(none)` ne reste que pour les requêtes sans aucun des trois headers. Le healthcheck s'identifie avec le user-agent `ofm-healthcheck` (au lieu du `python-requests` par défaut) et son trafic interne (~288 req/jour) est exclu des stats.

- 2026-08-21 — `/data/ofm/http_host/logs_nginx/` n'est plus effacé au redeploy : l'historique des access logs alimente les stats d'origines (fenêtre de 14 jours assurée par logrotate).

- 2026-08-17 — Home de démo : sélecteur de style dans le panneau (liste prédéfinie : `winter`, `summer`, `winter-hillshade-mix`) au lieu du style `winter` figé. Le style choisi est conservé dans l'URL (`?style=…`, compatible avec le hash de position MapLibre) pour permettre de partager un lien ; une valeur inconnue retombe sur `winter`.

- 2026-08-17 — Cron de sync (`ofm_http_host`) restreint à une fenêtre nocturne (`* 0-4 * * *`, heure locale serveur) : les téléchargements planet (~90 Go + extraction, gros I/O disque) ne peuvent plus dégrader le service de tuiles en journée. Contrepartie : les mises à jour arrivent avec jusqu'à ~24 h de retard et une panne du sync détectée en journée ne se répare que la nuit suivante (le healthcheck Slack alerte entre-temps). Aligné avec la modification faite manuellement sur tiles.ublo.app le 2026-08-17.

- 2026-07-13 — Les styles (`/styles/{name}`) sont désormais cachés **5 minutes** au lieu d'1 jour : ils évoluent régulièrement et le `sub_filter` nginx empêche la revalidation conditionnelle (ETag/Last-Modified supprimés).

### Fixed

- 2026-08-24 — `/planet` ne renvoie plus 403 quand la version `deployed` n'est pas disponible localement (téléchargement en cours ou échoué) : `create_latest_locations` sert alors `/{area}` depuis le run monté le plus récent — version périmée mais servie — au lieu d'omettre le bloc `location = /{area}`. C'est le mécanisme exact de la panne de juillet-août 2026 ; il devient une dégradation signalée par le healthcheck (écart version servie / version deployed) au lieu d'une panne.

- `ssh_lib/pkg_base.py` : retrait du paquet `ctop` de la liste des paquets de base. Il n'est plus disponible dans les dépôts Ubuntu récents (26.04 « resolute ») et faisait échouer `apt-get install` (donc `prepare_shared`) lors du déploiement http-host.
