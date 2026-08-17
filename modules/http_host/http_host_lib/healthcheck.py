import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from http_host_lib.config import config


# a tile over Paris, z14 is the max zoom of the OFM planet
SAMPLE_TILE = dict(z=14, x=8297, y=5637)

REALERT_HOURS = 24

DEFAULT_MIN_FREE_GB = 300


def run_healthcheck() -> int:
    """
    Checks that tiles are served correctly and alerts a Slack channel on failure.
    Designed to be run by cron every few minutes: state is kept in a JSON file,
    a Slack message is only sent on state change (failure/recovery),
    plus a daily reminder while the failure lasts.

    Returns 0 if everything is fine, 1 otherwise (usable manually).
    """

    issues = []

    domain = config.ofm_config.get('domain_direct')
    if not domain:
        issues.append('domain_direct absent de config.json, healthcheck HTTP impossible')
    else:
        for area in config.areas:
            if area == 'planet' and config.ofm_config.get('skip_planet'):
                continue
            issues += check_area(domain, area)

    issues += check_disk_space()

    notify(domain or 'http-host', issues)

    return 1 if issues else 0


def check_area(domain: str, area: str) -> list[str]:
    """
    Checks the "latest" TileJSON of an area, a sample tile taken from it,
    and that the served version matches the deployed version file
    (a mismatch means the sync is stuck, e.g. out of disk space).
    """

    verify_certs = not config.ofm_config.get('self_signed_certs')

    tilejson_url = f'https://{domain}/{area}'
    try:
        r = requests.get(tilejson_url, timeout=10, verify=verify_certs)
    except Exception as e:
        return [f'{tilejson_url} injoignable ({e.__class__.__name__})']
    if r.status_code != 200:
        return [f'{tilejson_url} repond HTTP {r.status_code} au lieu de 200']

    try:
        tiles_template = r.json()['tiles'][0]
    except Exception:
        return [f'{tilejson_url} ne renvoie pas un TileJSON valide']

    issues = []

    tile_url = tiles_template
    for k, v in SAMPLE_TILE.items():
        tile_url = tile_url.replace('{' + k + '}', str(v))
    try:
        r = requests.get(tile_url, timeout=10, verify=verify_certs)
        if r.status_code != 200:
            issues.append(f'tuile {tile_url} repond HTTP {r.status_code} au lieu de 200')
    except Exception as e:
        issues.append(f'tuile {tile_url} injoignable ({e.__class__.__name__})')

    # tiles_template looks like https://domain/{area}/{version}/{z}/{x}/{y}.pbf
    try:
        served_version = tiles_template.split(f'/{area}/')[1].split('/')[0]
        deployed_version = (config.deployed_versions_dir / f'{area}.txt').read_text().strip()
    except Exception:
        return issues  # versioned URL or version file unavailable, skip this check

    if served_version != deployed_version:
        issues.append(
            f'{area}: version servie {served_version} != version deployed'
            f' {deployed_version} (sync bloque ? voir logs/http_host_sync.log)'
        )

    return issues


def check_disk_space() -> list[str]:
    """
    Early warning, before the sync starts failing: a planet download needs
    about 3x the size of the .gz in free space (~280 GB in Aug 2026, growing).
    Threshold configurable with HEALTHCHECK_MIN_FREE_GB in config/.env.
    """

    min_free_gb = config.ofm_config.get('healthcheck_min_free_gb') or DEFAULT_MIN_FREE_GB
    # fallback for local testing, where /data/ofm doesn't exist
    base_dir = config.http_host_dir if config.http_host_dir.exists() else Path('/')
    free_gb = shutil.disk_usage(base_dir).free / 1e9
    if free_gb < min_free_gb:
        return [
            f'espace disque faible: {free_gb:.0f} GB libres < {min_free_gb} GB,'
            ' le prochain telechargement planet risque d\'echouer'
        ]
    return []


def notify(domain: str, issues: list[str]):
    state_file = config.http_host_dir / 'healthcheck_state.json'
    now = datetime.now(timezone.utc)

    try:
        state = json.loads(state_file.read_text())
    except Exception:
        state = {'failing': False, 'since': None, 'last_alert': None}

    if issues:
        print(f'{now.isoformat()} KO: {issues}')

        realert_due = True
        if state['last_alert']:
            last_alert = datetime.fromisoformat(state['last_alert'])
            realert_due = now - last_alert > timedelta(hours=REALERT_HOURS)

        if not state['failing'] or realert_due:
            since = state['since'] if state['failing'] else now.isoformat()
            lines = '\n'.join(f'- {i}' for i in issues)
            sent = send_slack(f':rotating_light: *[{domain}] panne detectee*\n{lines}')
            state = {
                'failing': True,
                'since': since,
                # if Slack is unreachable, leave last_alert unset so we retry next run
                'last_alert': now.isoformat() if sent else state.get('last_alert'),
            }
    else:
        print(f'{now.isoformat()} OK')
        if state['failing']:
            send_slack(f':white_check_mark: *[{domain}] retour a la normale* (panne depuis {state["since"]})')
        state = {'failing': False, 'since': None, 'last_alert': None}

    try:
        state_file.write_text(json.dumps(state))
    except OSError as e:  # local testing without /data/ofm
        print(f'  etat non persiste: {e}')


def send_slack(text: str) -> bool:
    token = config.ofm_config.get('slack_bot_token')
    channel = config.ofm_config.get('slack_channel')
    if not token or not channel:
        print('  slack_bot_token/slack_channel absents de config.json, alerte non envoyee:')
        print(text)
        return False

    try:
        r = requests.post(
            'https://slack.com/api/chat.postMessage',
            headers={'Authorization': f'Bearer {token}'},
            json={'channel': channel, 'text': text},
            timeout=10,
        )
        data = r.json()
        if not data.get('ok'):
            print(f'  erreur Slack: {data.get("error")}')
            return False
        return True
    except Exception as e:
        print(f'  erreur Slack: {e.__class__.__name__} {e}')
        return False
