import re
import subprocess
import sys
from pathlib import Path

from http_host_lib.config import config


BLOCKLIST_FILE = config.http_host_dir / 'config' / 'blocked_origins.txt'  # persistant
NGINX_MAP_FILE = Path('/data/nginx/config/ofm_blocked.conf')  # genere
TEMPLATE = config.nginx_confs / 'ofm_blocked.conf'

DOMAIN_RE = re.compile(r'^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$')

BLOCKLIST_HEADER = (
    '# Domaines bloques (Origin/Referer), un par ligne, sous-domaines inclus.\n'
    '# Gere par http_host.py block/unblock ; edition manuelle possible,\n'
    '# puis appliquer avec : sudo http_host.py nginx-config\n'
)


def normalize_domain(raw: str) -> str:
    domain = raw.strip().lower()
    domain = re.sub(r'^[a-z][a-z0-9+.-]*://', '', domain)  # scheme://
    domain = domain.split('/')[0].split(':')[0]  # path, port
    domain = domain.removeprefix('*.').lstrip('.')

    if not DOMAIN_RE.fullmatch(domain):
        sys.exit(f"domaine invalide: '{raw}' (IDN: utiliser la forme punycode)")

    return domain


def read_blocklist() -> list[str]:
    if not BLOCKLIST_FILE.is_file():
        return []

    domains = []
    for line in BLOCKLIST_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        domains.append(line)
    return domains


def write_map_conf():
    entries = '\n'.join(f'    .{d} 1;' for d in read_blocklist())
    conf = TEMPLATE.read_text().replace('__BLOCKED_ENTRIES__', entries)

    try:
        NGINX_MAP_FILE.write_text(conf)
    except PermissionError:
        sys.exit(f'  permission refusee sur {NGINX_MAP_FILE}, lancer avec sudo')

    print(f'  {NGINX_MAP_FILE} ecrit ({len(read_blocklist())} domaine(s) bloque(s))')


def reload_nginx():
    subprocess.run(['nginx', '-t'], check=True)
    subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
    print('  nginx recharge')


def block_domain(raw: str):
    domain = normalize_domain(raw)

    if domain in read_blocklist():
        print(f'{domain} est deja bloque')
        return

    text = BLOCKLIST_FILE.read_text() if BLOCKLIST_FILE.is_file() else BLOCKLIST_HEADER
    if text and not text.endswith('\n'):
        text += '\n'
    try:
        BLOCKLIST_FILE.write_text(text + domain + '\n')
    except (PermissionError, FileNotFoundError):
        sys.exit(f'  ecriture impossible dans {BLOCKLIST_FILE}, lancer avec sudo')

    print(f'{domain} ajoute a la blocklist (sous-domaines inclus)')
    write_map_conf()
    reload_nginx()


def unblock_domain(raw: str):
    domain = normalize_domain(raw)

    if domain not in read_blocklist():
        print(f"{domain} n'est pas dans la blocklist")
        return

    lines = BLOCKLIST_FILE.read_text().splitlines()
    kept = [l for l in lines if l.strip() != domain]
    BLOCKLIST_FILE.write_text('\n'.join(kept) + '\n')

    print(f'{domain} retire de la blocklist')
    write_map_conf()
    reload_nginx()


def list_blocked():
    domains = read_blocklist()
    if not domains:
        print('(aucun domaine bloque)')
        return
    for d in domains:
        print(d)
