# Self-hosting Howto

You can either self-host or use our public instance. Everything is **open-source**, including the full production setup — there’s no 'open-core' model here.

When self-hosting, there are two modules you can set up on a server (see details in the repo README).

- **http-host**

- **tile-gen**

There is a 99.9% chance you only need **http-host**. Tile-gen is slow, needs a huge machine and is totally pointless, since we upload the processed files every week.

### System requirements

**http-host**: 300 GB disk space for hosting a single run. SSD is recommended, but not required.

> Note: the sync requires roughly `3 × compressed_planet_size` of free space before downloading (compressed `.gz` + uncompressed btrfs image held at the same time). The planet `.gz` is currently ~96 GB, so ~290 GB free is needed for a single run. In **autoupdate** mode the weekly sync downloads the new version while the previous one is still mounted (cleanup runs afterwards), so plan for extra headroom — 350–400 GB is a safer target.

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

The root URL (`https://DOMAIN/`) serves a MapLibre demo map on the `winter` style instead of upstream's redirect to openfreemap.org. It is protected by basic auth: set `DEMO_AUTH_USER` / `DEMO_AUTH_PASS` in `config/.env` (empty credentials lock the page entirely, 401 for everyone — nginx keeps working).

- Page shipped in `modules/http_host/demo/index.html`, uploaded at deploy to `/data/ofm/http_host/assets/demo/`.
- htpasswd generated at deploy (`openssl passwd -apr1`) into `/data/nginx/htpasswd_demo`.
- Only `/` is protected — tiles, styles, sprites and fonts stay public (the map on the page loads them anonymously).

## Healthcheck with Slack alerting (this fork)

A cron task (`/etc/cron.d/ofm_healthcheck`, every 5 minutes) runs `http_host.py healthcheck` on the server, which checks:

- `https://DOMAIN/planet` returns HTTP 200 with a valid TileJSON (and `/monaco` too);
- a sample tile from that TileJSON returns HTTP 200;
- the served version matches `/data/ofm/config/deployed_versions/{area}.txt` — a mismatch means the sync is stuck (typically out of disk space);
- free disk space is above `HEALTHCHECK_MIN_FREE_GB` (default 300 GB — a planet download needs about 3× the size of the `.gz` in free space).

On failure it posts to a Slack channel via a bot token (`chat.postMessage`), then stays quiet: one message per state change (failure/recovery) plus a daily reminder while the failure lasts. State is kept in `/data/ofm/http_host/healthcheck_state.json`, logs in `/data/ofm/http_host/logs/healthcheck.log`.

Setup:

1. Create a Slack app with the `chat:write` scope, install it in the workspace, invite the bot to the target channel (or give it `channels:join`).
2. Set `SLACK_BOT_TOKEN` and `SLACK_CHANNEL` (channel ID, not name) in `config/.env`. Leaving them empty disables the cron at deploy time.
3. Redeploy (`./init-server.py http-host-autoupdate HOSTNAME`), or run `http_host.py healthcheck` manually on the server to test.

---

#### Deploy tile-gen server (optional)

If you have a really beefy machine (see above) and you really want to generate tiles yourself, you can run `./init-server.py tile-gen HOSTNAME`.

Trigger a run manually, by running

```
sudo /data/ofm/venv/bin/python -u /data/ofm/tile_gen/bin/tile_gen.py make-tiles planet
```

It's recommended to use tmux or similar, as it can take days to complete.
