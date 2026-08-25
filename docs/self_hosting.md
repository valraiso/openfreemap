# Self-hosting Howto

You can either self-host or use our public instance. Everything is **open-source**, including the full production setup — there’s no 'open-core' model here.

When self-hosting, there are two modules you can set up on a server (see details in the repo README).

- **http-host**

- **tile-gen**

There is a 99.9% chance you only need **http-host**. Tile-gen is slow, needs a huge machine and is totally pointless, since we upload the processed files every week.

### System requirements

**http-host**: 300 GB disk space for hosting a single run. SSD is recommended, but not required.

> Note: a download needs the compressed `.gz` and the uncompressed btrfs image at the same time, so it requires `gz_size + image_size` of free space — ~270 GB in Aug 2026 (`.gz` 98 GB, image 164 GB), and growing every week. In **autoupdate** mode the new version is downloaded while the previous one is still mounted (cleanup runs afterwards), so the volume has to hold one run plus that peak: ~450 GB usable is the realistic minimum, with [`SINGLE_PLANET=true`](#keeping-a-single-planet-run-this-fork). Upstream's default (two runs kept) needs roughly 150 GB more.

**tile-gen**: 500 GB SDD and at least 64 GB ram

**Ubuntu 22** or newer

### Provider recommendation

One amazing deal, which is tested and known to work well for http-host is the €4.5 / month [Contabo Storage VPS](https://contabo.com/en/storage-vps/)

---

### Warning

This project is made to run on **clean servers** or virtual machines dedicated for this project. The scripts need sudo permissions as they mount/unmount disk images. Do not run this on your dev machine without using virtual machines. If you do, please make sure you understand exactly what each script is doing.

If you run it on a non-clean server, please understand that this will modify your nginx config!

---

## Instructions

I recommend running things quickly first, with `SKIP_PLANET=true` and then once it works, running it with `SKIP_PLANET=false`.

#### 1. DNS setup

Set up a server with at least 300 GB SSD space and configure the DNS for the subdomain of your choice.
For example, make an A record for "maps.example.com" -> 185.199.110.153

#### 2. Clone and prepare `config` folder

```
git clone https://github.com/hyperknot/openfreemap
```

In the config folder, copy `.env.sample` to `.env` and set the values.

`DOMAIN_DIRECT` - Your subdomain \
`LETSENCRYPT_EMAIL` - Your email for Let's Encrypt

Set `SKIP_PLANET=true` first.

#### 3. Set up Python if you don't have it yet

On Ubuntu you can get it by `sudo apt install python3-pip`

On macOS you can do `brew install python`

#### 4. Prepare the Python environment

You run the deploy script locally, and it deploys to a remote server over SSH. You can use a virtualenv if you are used to working with them, but it's not necessary.

```
cd openfreemap
pip install -e .
```

#### 5. Deploy quick version with `SKIP_PLANET=true`

Run the actual deploy command and wait a few minutes

```
./init-server.py http-host-static HOSTNAME
```

#### 5. Check

If everything is OK, you'll have some curl lines printed. Run the first one locally and make sure it's showing HTTP/2 200. For example this is an OK response.

```locally to test them.
curl -sI https://test.openfreemap.org/monaco | sort

HTTP/2 200
access-control-allow-origin: *
cache-control: max-age=86400
cache-control: public
content-length: 5776
content-type: application/json
date: Fri, 11 Oct 2024 21:01:23 GMT
etag: "670991d1-1690"
expires: Sat, 12 Oct 2024 21:01:23 GMT
last-modified: Fri, 11 Oct 2024 21:00:01 GMT
server: nginx
x-ofm-debug: latest JSON monaco
```

#### 6. Deploy and check with `SKIP_PLANET=false`

Update your `.env` file and re-run the same `./init-server.py http-host-static HOSTNAME` as before.

Go for a walk and by the time you come back it should be up and running with the latest planet tiles deployed. Don't worry about the "Download aborted" lines in the meanwhile, it's a bug in CloudFlare.

If your server doesn't have an SSD, the download + uncompressing process can take hours.

---

## Custom assets (this fork)

This fork's http-host also serves **custom assets** on top of the standard OFM tiles: sprites, additional tile datasets and MapLibre styles. This repo neither produces nor deploys these assets — an external process deposits them on the server; nginx serves them via generic locations, so **adding a new dataset, style or sprite requires no nginx reload and no redeploy**.

The minutely OFM sync never touches these locations: it only rewrites `assets/{fonts,styles,natural_earth}/ofm/`, adds sprite versions under `assets/sprites/`, and manages `runs/` + `/mnt/ofm`.

### How to deposit files

- Deposit as user `ofm` (or `chown ofm:ofm` afterwards), directories `755`, files `644` (nginx runs as user `nginx` and only needs read access).
- Deposit **atomically**: extract/copy into a temporary name next to the target, then `mv` it into place.

### Custom sprites

Deposit into `/data/ofm/http_host/assets/sprites/{name}/`, served at `https://DOMAIN/sprites/{name}/...`. Sprites are cached for 10 years — if a sprite can change, put a version in its directory name (like OFM's `ofm_f384`) and update the styles referencing it.

### Custom tile datasets

Deposit a tile pyramid into `/data/ofm/http_host/tiles/{dataset}/{z}/{x}/{y}.{ext}`, served at `https://DOMAIN/tiles/{dataset}/{z}/{x}/{y}.{ext}`. Both vector and raster datasets are supported:

- **Vector** (`.pbf`): tiles must be **pre-gzipped**, like OFM tiles (this is tippecanoe's default with `--output-to-directory`); they are served with `Content-Encoding: gzip`. Missing tiles return an empty `200` response, same as OFM tiles.
- **Raster** (`.webp`, `.png`, `.jpg`, `.jpeg`, `.avif`): tiles are served as-is (no gzip — these formats are already compressed), with the MIME type matching the extension. Missing tiles return `404` (an empty `200` would be an image decode error client-side).
- Datasets are treated as **immutable** (tiles cached for 10 years): put a version in the dataset name (e.g. `pistes-20260713`) instead of updating tiles in place.
- Optionally deposit a `tilejson.json` at the dataset root: it is then served at `https://DOMAIN/tiles/{dataset}` (cached 1 day). Use the literal placeholder `__TILEJSON_DOMAIN__` in its URLs; nginx substitutes the configured domain when serving.

### Custom MapLibre styles

Deposit `{name}.json` files into `/data/ofm/http_host/assets/styles/custom/`, served at `https://DOMAIN/styles/{name}`.

- Custom styles take precedence over OFM styles with the same name.
- Use the literal placeholder `__TILEJSON_DOMAIN__` for the domain in `sources`, `sprite` and `glyphs` URLs, like the OFM styles do.
- Styles are cached for **5 minutes** only, since they are expected to change regularly.
- Do NOT put custom files inside `assets/styles/ofm/` — that directory is wiped by the minutely sync. (Theoretical caveat: if the upstream styles tarball ever contained a `custom/` entry, it would overwrite this directory.)

Test requests for all these endpoints are in [`examples/requests.http`](../examples/requests.http).

## Demo home page (this fork)

The root URL (`https://DOMAIN/`) serves a MapLibre demo map instead of upstream's redirect to openfreemap.org. A selector in the panel switches between the predefined styles (`winter`, `summer`, `winter-hillshade-mix`); the current choice is kept in the `?style=` query parameter so the link can be shared (an unknown value falls back to `winter`). It is protected by basic auth: set `DEMO_AUTH_USER` / `DEMO_AUTH_PASS` in `config/.env` (empty credentials lock the page entirely, 401 for everyone — nginx keeps working).

- Page shipped in `modules/http_host/demo/index.html`, uploaded at deploy to `/data/ofm/http_host/assets/demo/`.
- htpasswd generated at deploy (`openssl passwd -apr1`) into `/data/nginx/htpasswd_demo`.
- Only `/` is protected — tiles, styles, sprites and fonts stay public (the map on the page loads them anonymously).

## Healthcheck with Slack alerting (this fork)

A cron task (`/etc/cron.d/ofm_healthcheck`, every 5 minutes) runs `http_host.py healthcheck` on the server, which checks:

- `https://DOMAIN/planet` returns HTTP 200 with a valid TileJSON (and `/monaco` too);
- a sample tile from that TileJSON returns HTTP 200;
- the served version matches `/data/ofm/config/deployed_versions/{area}.txt` — a mismatch means the sync is stuck (typically out of disk space) and `/planet` is serving a stale run;
- free disk space is enough for the **next** planet download, computed from the size of the newest remote `.gz` plus the local image size (~270 GB in Aug 2026, growing every week). `HEALTHCHECK_MIN_FREE_GB` in `config/.env` is the fallback threshold, used only when that size cannot be fetched (default 300 GB).

On failure it posts to a Slack channel via a bot token (`chat.postMessage`), then stays quiet: one message per state change (failure/recovery) plus a daily reminder while the failure lasts. State is kept in `/data/ofm/http_host/healthcheck_state.json`, logs in `/data/ofm/http_host/logs/healthcheck.log`.

Setup:

1. Create a Slack app with the `chat:write` scope, install it in the workspace, invite the bot to the target channel (or give it `channels:join`).
2. Set `SLACK_BOT_TOKEN` and `SLACK_CHANNEL` (channel ID, not name) in `config/.env`. Leaving them empty disables the cron at deploy time.
3. Redeploy (`./init-server.py http-host-autoupdate HOSTNAME`), or run `http_host.py healthcheck` manually on the server to test.

## Keeping a single planet run (this fork)

Upstream keeps **two** planet runs on disk (the deployed one and the newest one). On a 500 GB volume that leaves too little free space for the next download, so the sync skips the update every night — silently, since a skipped download is not an error.

Set `SINGLE_PLANET=true` in `config/.env` and redeploy to keep **only the run that is actually served**:

- `auto_clean_btrfs` keeps the `deployed` planet run only. While that run is not downloaded yet, the newest local run is kept instead — the last servable run is never deleted. `monaco` is unaffected (its runs are 275 MB).
- the sync no longer downloads the planet `latest` run, only `deployed`: a second ~150 GB run would be deleted by the cleanup right away and re-downloaded the next night.
- the free-space requirement checked before a download is computed from reality — `gz size + local image size × 1.05` — instead of upstream's `3 × gz`. The peak usage is the `.gz` plus the uncompressed image, which coexist until `unpigz` is done. With no local image to compare with (fresh server), `3 × gz` is used.

Figures on 2026-08-24: planet `.gz` 97.8 GB, image 163.8 GB → 270 GB actually needed. With two runs kept, 138 GB were free (update impossible); with one run, 300 GB (update possible).

Two consequences to be aware of:

- **No spare run.** If the served image is corrupt, the fix is a re-download, not a switch back to the previous version.
- **The version transition is the peak**: old run mounted + new `.gz` + new image. That peak is what sizes the volume, and it grows with the planet — the healthcheck disk alert (below) is what warns you before it stops fitting.

Independently of this option, `/{area}` now degrades instead of failing: when the deployed run is not available locally, nginx serves the newest mounted run (stale) rather than dropping the `location = /{area}` block and returning **403**. The healthcheck reports the served/deployed mismatch, so a stuck sync is an alert, not an outage.

## Checking whether the OFM sync ran and succeeded (this fork)

In **autoupdate** mode the host picks up new OFM runs by itself (cron `ofm_http_host`, restricted to a night window in this fork). Nothing reports success anywhere, so here is how to tell "attempted" from "succeeded", from the cheapest signal to the most conclusive.

### 1. Was a sync attempted?

```
sudo journalctl -u cron --since today | grep 'http_host.py sync' | tail -3
sudo journalctl -u cron --since today | grep -c 'http_host.py sync'
```

cron logs every launch, even when the run itself prints nothing, and the journal survives redeploys — this is the reliable "it was attempted" signal. Expect one entry per minute of the window (`* 0-4 * * *` server local time, so ~300/day; the journal may print each line twice). Zero entries means the cron is not there: check `cat /etc/cron.d/ofm_http_host` (a `http-host-static` deploy installs no sync cron at all).

`flock -n` allows only one sync at a time, so while a long planet download runs, the launches of the following minutes exit immediately and do nothing. That is expected.

### 2. What did the sync actually do?

```
sudo tail -50 /data/ofm/http_host/logs/http_host_sync.log
```

Each run starts with `---`, a UTC timestamp and `Starting sync`, then prints one line per area/version:

| Line                                                                    | Meaning                                                                                                                              |
| ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `file exists, skipping download`                                        | already present locally, nothing to do — the normal case for most runs                                                               |
| `not enough disk space. Needed: N, free space: M`                       | update attempted and **failed**; a download needs the `.gz` plus the uncompressed image at once (~270 GB for the planet in Aug 2026) |
| `cannot get remote file size for …`                                     | bucket unreachable, or the version disappeared upstream                                                                              |
| aria2 progress, then `uncompressing...`                                 | download succeeded                                                                                                                   |
| `Running auto clean btrfs`, `keeping runs for …`, `removing runs for …` | something changed → cleanup, remount and nginx config rewrite ran                                                                    |

Two things to keep in mind:

- **A run that ends without `Running auto clean btrfs` changed nothing.** A skipped download is not an error: no exception is raised, the sync just moves on. "No traceback" therefore does not mean "planet updated".
- This file is only written by the cron (a manual run prints to the terminal instead), and `/data/ofm/http_host/logs/` is **wiped on every redeploy** — right after a deploy the file is absent until the next night's run. Fall back to steps 1 and 3 in that case.

### 3. Which version is expected, present, served?

```
# version OFM says should be deployed (refetched at every sync)
sudo cat /data/ofm/config/deployed_versions/planet.txt
# versions actually on disk
sudo ls -l /data/ofm/http_host/runs/planet /data/ofm/http_host/runs/monaco
# version actually served
curl -s https://DOMAIN/planet | head -c 120
# upstream, for comparison: deployed pointer, then all available runs
curl -s https://assets.openfreemap.com/deployed_versions/planet.txt
curl -s https://btrfs.openfreemap.com/files.txt | grep '^areas/planet/.*/done$' | tail -3
```

How to read it:

- served == local `deployed_versions/planet.txt` == upstream deployed pointer → up to date, nothing to do.
- the local deployed file names a version **absent** from `runs/planet/` → the sync is stuck: `write_nginx_config` then skips the `location = /planet` block, so `/planet` returns **403** while the versioned URLs keep working. This was the July–August 2026 outage.
- newest directory in `runs/planet/` older than the newest `done` version upstream → the nightly download of `latest` keeps failing (disk space, most likely). Invisible from the outside for now, but it turns into the 403 above as soon as OFM moves its deployed pointer to that version — so this is the state to catch early.

### 4. Passive monitoring

The [healthcheck](#healthcheck-with-slack-alerting-this-fork) cron (every 5 min) alerts on Slack and logs to `/data/ofm/http_host/logs/healthcheck.log`. It catches the two states that matter: the served version drifting from the deployed one (stuck sync, stale planet) and free space dropping below what the next planet download needs — the second one fires _before_ the first, so an alert about disk space is the signal to act.

What it does **not** cover: it compares the served version with the local deployed file, not with the newest version available upstream. As long as OFM has not promoted a new run, a host unable to download anything looks healthy apart from the disk alert.

### Forcing a sync outside the night window

```
sudo -u ofm /usr/bin/flock -n /tmp/http_host.lockfile -c \
  'sudo /data/ofm/venv/bin/python -u /data/ofm/http_host/bin/http_host.py sync'
```

Run it as `ofm`, not as root: the lockfile in `/tmp` belongs to `ofm` and `fs.protected_regular` prevents root from opening it. Add `--force` after `sync` to redo the mount + nginx config pass even when no version changed. Output goes to the terminal (not to `http_host_sync.log`), so use `tmux` if a real planet download may start — it takes hours.

## Origin statistics & abusive-origin blocking (this fork)

The nginx access log is enabled (upstream has it off), **without any IP address** — the only client attribution is the `Origin` and `Referer` headers, which identify the _website_ using the tiles, not the visitor. Logs are JSON lines in `/data/ofm/http_host/logs_nginx/le-access.jsonl` (fields: time, status, bytes, dataset, blocked flag, origin, referer, user-agent), rotated daily by logrotate with 14 days retention. This directory is no longer wiped on redeploy.

### Statistics

- On demand, on the server: `sudo -u ofm /data/ofm/venv/bin/python /data/ofm/http_host/bin/http_host.py stats [--days N]` — table of requests / GB / blocked / statuses per origin, plus per-dataset counts. Attribution: Origin host, falling back to the Referer host, then to the user-agent (shown as `(ua) …`, truncated to 40 chars); `(none)` when all three are absent. The healthcheck identifies itself with the `ofm-healthcheck` user-agent and its self-traffic is excluded from the stats.
- Weekly Slack report: cron `/etc/cron.d/ofm_stats_report` (Monday 08:05, server local time) posts the last 7 days to the same Slack channel as the healthcheck. Installed at deploy only when `SLACK_BOT_TOKEN`/`SLACK_CHANNEL` are set in `config/.env`.

### Blocking an abusive origin

```
sudo /data/ofm/venv/bin/python /data/ofm/http_host/bin/http_host.py block evil.example
sudo /data/ofm/venv/bin/python /data/ofm/http_host/bin/http_host.py unblock evil.example
sudo /data/ofm/venv/bin/python /data/ofm/http_host/bin/http_host.py blocked   # list
```

Requests whose `Origin` **or** `Referer` host matches a blocked domain get a `403` (logged with `"blocked": 1`). Semantics:

- blocking a domain also blocks **all its subdomains**;
- matching is case-insensitive, scheme and port are ignored;
- enter IDN domains in punycode form (`xn--…`);
- this targets abusive _websites_ — a non-browser scraper can omit or spoof these headers, this is not a DDoS defense.

The list lives in `/data/ofm/http_host/config/blocked_origins.txt` (one domain per line, `#` comments allowed) and **survives redeploys**. It can also be edited by hand, then applied with `sudo … http_host.py nginx-config`. The nginx `map` file (`/data/nginx/config/ofm_blocked.conf`) is generated from it on every deploy/sync/block/unblock — never edit that one.

---

#### Deploy tile-gen server (optional)

If you have a really beefy machine (see above) and you really want to generate tiles yourself, you can run `./init-server.py tile-gen HOSTNAME`.

Trigger a run manually, by running

```
sudo /data/ofm/venv/bin/python -u /data/ofm/tile_gen/bin/tile_gen.py make-tiles planet
```

It's recommended to use tmux or similar, as it can take days to complete.
