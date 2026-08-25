# Un seul run planet conservé (celui servi) — 2026-08-24

## Problème

`auto_clean_btrfs` garde deux runs planet (la version `deployed` **et** la plus récente locale). Sur le volume de 500 Go de `tiles.ublo.app` :

| | Octets | Go |
| --- | --- | --- |
| Image planet locale (`tiles.btrfs`) | 163 784 572 928 | 164 |
| × 2 runs conservés | | 327 |
| Espace libre constaté le 2026-08-24 | 137 538 002 944 | 138 |
| `.gz` du planet distant `20260823_080002_pt` | 97 837 935 892 | 98 |
| Besoin selon le critère upstream (`3 × .gz`) | 293 513 807 676 | 294 |

→ le téléchargement du nouveau planet est sauté chaque nuit, sans erreur (`download_and_extract_btrfs` retourne `False`). C'est la panne de juillet-août 2026, et la rechute constatée le 2026-08-24 (`not enough disk space` dans le log de sync).

Le pic réel d'un téléchargement n'est pas `3 × .gz` : c'est le `.gz` **plus** l'image décompressée, qui coexistent le temps de `unpigz`, soit 98 + 164 ≈ **270 Go**.

## Changements

1. **`SINGLE_PLANET=true`** (`config/.env` → `single_planet` dans `config.json`, via `upload_config_json`).
2. `auto_clean_btrfs` (`sync.py`) : pour `planet` en mode single, on ne garde que la version `deployed`. Repli sur le run local le plus récent si la version `deployed` n'est pas (encore) téléchargée — le dernier run servable n'est jamais supprimé. `monaco` inchangé.
3. `full_sync` : en mode single, plus de téléchargement du run `latest` du planet (il serait supprimé aussitôt par le nettoyage, donc re-téléchargé chaque nuit), seulement `deployed`.
4. `needed_space` (`btrfs.py`) : besoin = `.gz + image locale × 1,05`, repli `3 × .gz` sans image de référence. Le log affiche besoin et espace libre en Go.
5. `check_disk_space` (`healthcheck.py`) : compare l'espace libre au besoin de la prochaine MAJ planet (calculé sur le `.gz` distant le plus récent) ; `HEALTHCHECK_MIN_FREE_GB` devient le repli quand le bucket est injoignable.
6. Garde-fou nginx (indépendant du mode) : `create_latest_locations` sert `/{area}` depuis le run monté le plus récent quand la version `deployed` n'est pas disponible localement, au lieu d'omettre le bloc `location = /{area}` (403).

## Effet attendu sur tiles.ublo.app

| | Go libres | Besoin | MAJ possible |
| --- | --- | --- | --- |
| Avant (2 runs, critère `3 × .gz`) | 138 | 294 | non |
| Après (1 run, besoin réel) | 300 | 270 | oui, marge 30 Go |

Le pic pendant la transition (ancien run monté + `.gz` + nouvelle image) reste ce qui dimensionne le volume ; il grossit avec le planet, et l'alerte disque du healthcheck le signale avant blocage.

## Activation

1. `SINGLE_PLANET=true` dans `config/.env`.
2. `venv/bin/python init-server.py http-host-autoupdate tiles -y` — le deploy termine par `http_host.py sync --force`, donc le nettoyage (suppression du run `20260802`, umount, réécriture nginx) a lieu tout de suite ; l'espace n'est réellement rendu qu'au `umount` fait par `clean_up_mounts`.
3. Vérifier : `df -h /data` (~300 Go libres), `/planet` en 200 sur `20260816_080001_pt`, healthcheck OK.
4. La MAJ vers `20260823` se fera la nuit où OFM basculera son pointeur `deployed` (fenêtre 00:00-04:59), en ~1-3 h de téléchargement.

## Tests

Logique validée hors serveur (arbre de runs factice) : comportement upstream inchangé flag off, un seul run gardé flag on, repli quand `deployed` absent en local, aucun run supprimé quand il n'en reste qu'un, `monaco` non affecté, estimations `needed_space` avec et sans image de référence, repli nginx sur le run monté le plus récent.

## Limites acceptées

- Plus de run de secours : image servie corrompue = re-téléchargement.
- On ne suit plus que les versions promues `deployed` par OFM (plus d'anticipation via `latest`) — c'est le comportement voulu.
- Le mode reste optionnel (`false` par défaut) pour ne pas imposer la perte du run de repli aux autres self-hosters.
