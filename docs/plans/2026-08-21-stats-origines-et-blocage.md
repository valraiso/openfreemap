# Stats d'origines + blocage d'origines abusives (http-host tiles.ublo.app)

## Contexte

Le http-host (tiles.ublo.app) ne logge actuellement **rien** (`access_log off` partout — choix upstream pour la vie privée : pas d'IP) et n'a aucun mécanisme de blocage par origine. Besoin : savoir quels sites consomment les tuiles (headers `Origin`/`Referer`) et pouvoir bloquer un site abusif sans redéploiement. Décisions utilisateur : rapport Slack **hebdomadaire** + CLI à la demande ; blocage sur **Origin ET Referer** ; blocklist = fichier serveur persistant + commandes CLI. La politique « pas d'IP loggée » est conservée.

## Architecture

1. **Logging nginx** : activer l'access log JSON dans les templates de site, enrichir `log_format access_json` avec `$http_origin`, un `$ofm_dataset` compact (1er segment de path, évite de logger les URI complètes de tuiles) et un flag `$ofm_blocked`. Rotation logrotate quotidienne, rétention 14 jours (couvre toujours la fenêtre hebdo).
2. **Blocage nginx** : chaîne de `map` dans un fichier **généré** `/data/nginx/config/ofm_blocked.conf` (contexte http, déjà inclus par `include /data/nginx/config/*`), rendu depuis la liste **persistante** `/data/ofm/http_host/config/blocked_origins.txt`. Enforcement : `if ($ofm_blocked) { return 403; }` au niveau `server` (le seul pattern `if` universellement sûr — phase server-rewrite, avant le matching des locations).
3. **Python (http_host_lib)** : nouveaux modules `stats.py` (agrégation) et `blocklist.py` (gestion blocklist + rendu map + reload). Nouvelles commandes CLI : `stats`, `stats-report`, `block`, `unblock`, `blocked`. Nouveau cron `ofm_stats_report` (lundi matin, conditionné à Slack comme le healthcheck).

## Étapes

### 1. `ssh_lib/assets/nginx/nginx.conf` — log_format

Avant le `log_format`, ajouter la map dataset :

```nginx
# 1er(s) segment(s) de path = dataset/area, garde les lignes de log compactes
map $uri $ofm_dataset {
    default '';
    ~^/tiles/(?<d>[^/]+) 'tiles/$d';
    ~^/(?<a>[^/]+) $a;
}
```

Dans `access_json`, ajouter après `body_bytes_sent` :

```nginx
'"http_origin": "$http_origin", '
'"dataset": "$ofm_dataset", '
'"blocked": $ofm_blocked, '
```

Ne pas décommenter `uri`/`remote_addr` etc. — **pas d'IP**. `$ofm_blocked` est défini dans le fichier map inclus (nginx résout les variables en fin de parse, l'ordre des includes est OK — mais le fichier map doit exister, garanti par l'étape 6).

### 2. Activer l'access log dans les templates de site

[le.conf:26-28](modules/http_host/http_host_lib/nginx_confs/le.conf#L26-L28) — remplacer `access_log off;` + ligne commentée par :

```nginx
# access log sans adresse IP (cf log_format access_json dans /etc/nginx/nginx.conf)
access_log /data/ofm/http_host/logs_nginx/le-access.jsonl access_json buffer=64k flush=1m;
```

Idem dans `roundrobin.conf` → `roundrobin-access.jsonl` (cohérence, inutilisé sur tiles.ublo.app).

### 3. Nouveau template `modules/http_host/http_host_lib/nginx_confs/ofm_blocked.conf`

Source unique, utilisée par le générateur côté serveur ET par le fallback vide au deploy :

```nginx
# GENERE - ne pas editer.
# Source : /data/ofm/http_host/config/blocked_origins.txt
# Regenere par http_host.py nginx-config / block / unblock ;
# un fallback vide est installe au deploy par ssh_lib/nginx.py.

map $http_origin $ofm_origin_host {
    default '';
    ~*^https?://(?<h>[^/:]+) $h;
}

map $http_referer $ofm_referer_host {
    default '';
    ~*^https?://(?<h>[^/:]+) $h;
}

map $ofm_origin_host $ofm_blocked_origin {
    hostnames;
    default 0;
__BLOCKED_ENTRIES__
}

map $ofm_referer_host $ofm_blocked_referer {
    hostnames;
    default 0;
__BLOCKED_ENTRIES__
}

map "$ofm_blocked_origin$ofm_blocked_referer" $ofm_blocked {
    default 1;
    '00' 0;
}
```

Chaque entrée rend `    .example.com 1;` → **bloquer un domaine bloque aussi ses sous-domaines** (`hostnames;` + point initial ; `hostnames` doit précéder les entrées). Ports strippés par la regex d'extraction, matching insensible à la casse, `Origin: null` / schémas non-http jamais bloqués, IDN à entrer en punycode.

Enforcement dans `le.conf` **et** `roundrobin.conf`, en haut du `server {}` (après `error_log`, avant la location acme — les requêtes ACME n'envoient ni Origin ni Referer, non affectées) :

```nginx
# origines abusives, gerees via : sudo http_host.py block/unblock <domaine>
if ($ofm_blocked) {
    return 403;
}
```

Les 403 bloqués ne portent pas de header CORS → le navigateur affiche une erreur CORS + 403, c'est voulu et peu coûteux.

### 4. Nouveau `modules/http_host/http_host_lib/blocklist.py`

```python
BLOCKLIST_FILE = config.http_host_dir / 'config' / 'blocked_origins.txt'   # persistant
NGINX_MAP_FILE = Path('/data/nginx/config/ofm_blocked.conf')               # généré
TEMPLATE = config.nginx_confs / 'ofm_blocked.conf'
DOMAIN_RE = re.compile(r'^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$')

normalize_domain(raw)   # lowercase, strip scheme://, path, :port, '.' / '*.' initiaux ;
                        # sys.exit si pas DOMAIN_RE.fullmatch → empêche l'injection de conf nginx
read_blocklist()        # fichier absent → [] ; ignore commentaires '#' et lignes vides
write_map_conf()        # entries = '\n'.join(f'    .{d} 1;' ...) ; template.replace('__BLOCKED_ENTRIES__', entries)
                        # → NGINX_MAP_FILE ; PermissionError → sys.exit('lancer avec sudo')
reload_nginx()          # subprocess : nginx -t (check=True) puis systemctl reload nginx
block_domain(raw) / unblock_domain(raw)  # édite le txt (préserve les commentaires, crée le fichier
                        # avec en-tête si absent, no-op si déjà présent/absent), puis write_map_conf() + reload_nginx()
list_blocked()          # affiche la liste (ou '(vide)')
```

Hook dans [nginx.py](modules/http_host/http_host_lib/nginx.py) : dans `write_nginx_config()`, juste après le check `mnt_dir` (~ligne 14), appeler `write_map_conf()` → chaque deploy/`nginx-config`/sync régénère la map depuis la liste persistante **avant** tout `nginx -t`/reload.

### 5. Nouveau `modules/http_host/http_host_lib/stats.py` + CLI

```python
iter_records(days)      # glob logs_nginx/*access.jsonl* ; skip fichiers mtime < cutoff ;
                        # gzip.open pour *.gz (delaycompress garde .1 en clair) ;
                        # json.loads par ligne (skip lignes malformées) ;
                        # filtre datetime.fromisoformat(rec['time']) >= cutoff
origin_of(rec)          # http_origin → host (strip scheme/port, lowercase) ;
                        # non-parsable mais non vide (ex 'null') → valeur brute ;
                        # sinon host du referer ; sinon '(none)'
aggregate(days)         # {'total': {requests, bytes, blocked, statuses}, 'origins': {...}, 'datasets': Counter}
format_text(agg, top=25)   # table CLI alignée
format_slack(agg, top=15)  # totaux, top origines (req/GB/%bloqué), datasets, blocklist courante
run_stats_report()      # aggregate(7) → healthcheck.send_slack(format_slack(...)) ; retourne 0/1
                        # (send_slack gère déjà l'absence de token en imprimant)
```

Dans [http_host.py](modules/http_host/http_host.py), ajouter les commandes click : `stats` (`--days`, défaut 7), `stats-report` (`sys.exit(run_stats_report())`), `block <domain>`, `unblock <domain>`, `blocked` — même style que les commandes existantes.

Nouveau cron `modules/http_host/cron.d/ofm_stats_report` (hors fenêtre sync 0-4, workload read-only) :

```
5 8 * * 1 ofm     /usr/bin/flock -n /tmp/ofm_stats_report.lockfile -c '/data/ofm/venv/bin/python -u /data/ofm/http_host/bin/http_host.py stats-report >> /data/ofm/http_host/logs/stats_report.log 2>&1'
```

### 6. Câblage deploy

**Nouvel asset `ssh_lib/assets/nginx/logrotate_ofm_http_host`** :

```
/data/ofm/http_host/logs_nginx/*.jsonl /data/ofm/http_host/logs_nginx/*.log {
    daily
    rotate 14
    missingok
    notifempty
    compress
    delaycompress
    create 0644 nginx nginx
    sharedscripts
    postrotate
        [ -f /var/run/nginx.pid ] && kill -USR1 "$(cat /var/run/nginx.pid)"
    endscript
}
```

(`create 0644` → ofm peut lire pour les stats ; le logrotate du paquet nginx ne couvre que /var/log/nginx, pas de conflit ; timer systemd logrotate déjà actif sur Ubuntu.)

**[ssh_lib/nginx.py:47](ssh_lib/nginx.py#L47)** — après le `put(...cloudflare.conf...)`, installer le fallback vide. **Critique** : `nginx(c)` vient de vider `/data/nginx/config` puis fait `nginx -t` + restart (lignes 51-52) alors que les confs de sites persistées dans `/data/nginx/sites` et le `log_format` référencent `$ofm_blocked` → sans fallback, le deploy casse :

```python
# fallback blocklist vide : /data/nginx/config vient d'être vidé mais les confs
# de sites persistées et le log_format référencent $ofm_blocked ;
# la vraie map est régénérée depuis blocked_origins.txt par http_host.py
template = (
    MODULES_DIR / 'http_host' / 'http_host_lib' / 'nginx_confs' / 'ofm_blocked.conf'
).read_text()
put_str(c, '/data/nginx/config/ofm_blocked.conf', template.replace('__BLOCKED_ENTRIES__', ''))
```

(importer `MODULES_DIR` depuis `ssh_lib` ; ne PAS importer http_host_lib — son `config.py` charge config.json à l'import.)

**[ssh_lib/tasks.py](ssh_lib/tasks.py)** dans `prepare_http_host` :
- Lignes 97-99 : **ne plus effacer `logs_nginx`** — remplacer `rm -rf` par `mkdir -p` + chown seuls, pour que l'historique de stats survive aux redeploys.
- Bloc dirs persistants (lignes 101-107) : ajouter `mkdir -p /data/ofm/http_host/config` + chown ofm:ofm (le fichier blocklist est créé paresseusement par le CLI ; absent = liste vide).
- Ajouter `put(c, f'{ASSETS_DIR}/nginx/logrotate_ofm_http_host', '/etc/logrotate.d/ofm_http_host')` (mode 644 — logrotate refuse les configs world-writable).
- Bloc cron Slack (lignes 121-124) : ajouter `rm -f /etc/cron.d/ofm_stats_report` + `put` du cron quand `SLACK_BOT_TOKEN`/`SLACK_CHANNEL` sont définis.

Pas de changement `put_dir` : `nginx_confs/` et `http_host_lib/` sont déjà uploadés explicitement, les crons sont `put` individuellement. Note (quirk préexistant, inoffensif) : `chown -R ofm:ofm` en fin d'`upload_http_host_files` re-owne `logs_nginx` à ofm ; nginx (master root) écrit quand même et logrotate restaure `nginx nginx` à chaque rotation.

### 7. Docs / tests

- `CHANGELOG.md` (français) : entrée Added (stats + blocage : commandes, cron, logrotate, chemin blocklist persistant) + Changed (`logs_nginx` plus effacé au deploy, access log activé sans IP).
- `docs/self_hosting.md` : section « (this fork) Statistiques d'origines & blocage » — lecture du rapport hebdo, `stats --days N`, `sudo … http_host.py block evil.example` (sémantique sous-domaines, punycode), emplacement de la blocklist, survie aux redeploys.
- `docs/plans/DECISIONS.md` : **D003** — politique no-IP conservée ; blocage sur Origin ET host du Referer ; bloquer un domaine bloque ses sous-domaines ; 403 avec champ `blocked` dans les logs ; rotation quotidienne ×14 ; logs plus effacés au deploy.
- `examples/requests.http` : requêtes avec `Origin: https://evil.example` et `Referer: https://sub.evil.example/page` sur `https://tiles.ublo.app/planet` (403 attendu une fois bloqué, 200 sinon).
- Copie de ce plan dans `docs/plans/`.

## Vérification (bout en bout sur tiles.ublo.app)

1. Local : rendre le template avec 0 et 2 entrées et vérifier visuellement ; `python -m compileall modules/http_host`.
2. Deploy : `venv/bin/python init-server.py http-host-autoupdate tiles -y` — doit passer le `nginx -t` intermédiaire (test du fallback) et finir par `sync --force`.
3. Serveur : `nginx -T | grep -A3 ofm_blocked` montre les maps ; `/data/nginx/config/ofm_blocked.conf` régénéré ; `/etc/logrotate.d/ofm_http_host` présent ; `logrotate -d` dry-run propre.
4. `curl -sI https://tiles.ublo.app/planet -H 'Origin: https://good.example'` → 200 ; tail de `le-access.jsonl` : `http_origin`, `dataset: "planet"`, `blocked: 0`, **pas d'IP**.
5. `sudo /data/ofm/venv/bin/python /data/ofm/http_host/bin/http_host.py block evil.example` → reload OK ; curl avec `Origin: https://evil.example` → 403 ; avec `Referer: https://sub.evil.example/x` → 403 ; log `blocked: 1` ; `blocked` la liste ; `unblock` → 200 ; curl sans headers toujours 200.
6. `http_host.py stats --days 1` montre les deux origines (counts/bytes/blocked).
7. `http_host.py stats-report` poste dans Slack (lancé en ofm).
8. `sudo logrotate -f /etc/logrotate.d/ofm_http_host` → `.1` créé, nouveau `le-access.jsonl` 0644 nginx:nginx, nouveaux curls dedans ; `stats --days 1` voit toujours les lignes pré-rotation.
9. Redeploy complet : `blocked_origins.txt` et `logs_nginx/` intacts, map re-remplie après sync, crons présents.

## Risques identifiés

- **Volume de logs** : ~180-250 o/ligne ⇒ ~1 GB/jour brut à 5M req/jour, ~100 MB/jour gzippé, ≤ ~4 GB au total sur 14 jours — négligeable vs le stockage planet ; le healthcheck disque alerte déjà.
- **Perte de buffer** : `buffer=64k flush=1m` → perte ≤ 1 min uniquement sur crash dur (reload/USR1 flushent). Sans impact sur une fenêtre hebdo.
- **Ordre au deploy** = l'arête la plus fine : `$ofm_blocked` référencé par les confs persistées ET le log_format pendant que `/data/nginx/config` est vidé — le fallback (étape 6) est obligatoire, et `write_map_conf()` doit tourner avant le `nginx -t` de `write_nginx_config()`.
- **Syntaxe map** : `hostnames;` avant les entrées ; entrées validées par `DOMAIN_RE` (pas d'injection possible) ; `nginx -t` avant reload = filet (en cas d'échec l'ancienne conf reste servie).
- **Contournement** : Origin/Referer sont contrôlés par le client ; un scraper non-navigateur peut les omettre/usurper. Répond au besoin (sites web abusifs), pas une défense DDoS.
- Le changement de `nginx.conf` ne part que via un **deploy complet** (`service nginx restart`) — toujours passer par `init-server.py`, pas d'édition à la main sur le serveur.
