# Décisions structurantes

## D001 — Healthcheck serveur en Python dans le module http_host, alerte via bot Slack (2026-08-17)

**Décision** : le monitoring des pannes est une commande `http_host.py healthcheck` (module `http_host_lib/healthcheck.py`) lancée par un cron dédié sur le serveur, plutôt qu'un script shell séparé ou un service externe. L'alerte passe par l'API Slack `chat.postMessage` avec un bot token (`SLACK_BOT_TOKEN`/`SLACK_CHANNEL` dans `config/.env`, propagés dans `/data/ofm/config/config.json` par `upload_config_json`), sur le modèle des clés `telegram_*` déjà présentes upstream.

**Rationale** : réutilise la plomberie existante (venv, `config.json`, `requests`, déploiement des `cron.d/` par `init-server.py`), ce qui minimise la divergence avec l'upstream. Le check tourne sur le serveur lui-même : il détecte les 403 nginx, les incohérences version servie/deployed (cause de la panne d'août 2026, invisible de l'extérieur tant que la version tient) et l'espace disque — un moniteur externe type UptimeRobot ne voit que le premier cas et reste complémentaire. Anti-spam par fichier d'état (message au changement d'état + rappel quotidien) : un cron 5 min sans état enverrait 288 messages/jour de panne.

**Limite connue** : si le serveur entier tombe (machine, réseau), le healthcheck tombe avec lui et n'alerte pas — seul un moniteur externe couvre ce cas.

## D002 — Home de démo servie par nginx avec basic auth générée au deploy (2026-08-17)

**Décision** : la page de démo (`/`, carte MapLibre sur le style `winter`) est un fichier statique shippé dans le module (`modules/http_host/demo/index.html`) et uploadé au deploy dans `assets/demo/`, servi par un bloc `location = /` du template nginx avec `auth_basic`. Le htpasswd est généré au deploy par `openssl passwd -apr1` à partir de `DEMO_AUTH_USER`/`DEMO_AUTH_PASS` (`config/.env`, non versionné) vers `/data/nginx/htpasswd_demo` ; credentials absents → fichier vide, donc 401 systématique plutôt qu'un nginx cassé (le template référence le fichier inconditionnellement).

**Rationale** : mêmes canaux que le reste du fork (template nginx + `prepare_http_host`), pas de secret dans git, et seule la racine est protégée — les tuiles/styles restent publics car consommés anonymement par les applis. La fenêtre nocturne du cron de sync ne touche pas cette page (servie statiquement).
