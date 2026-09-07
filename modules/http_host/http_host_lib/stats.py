import gzip
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Iterator

from http_host_lib.blocklist import read_blocklist
from http_host_lib.config import config
from http_host_lib.healthcheck import HEALTHCHECK_USER_AGENT, USER_AGENT as HEALTHCHECK_UA
from http_host_lib.healthcheck import send_slack


LOGS_NGINX_DIR = config.http_host_dir / 'logs_nginx'

HOST_RE = re.compile(r'^https?://(?P<h>[^/:]+)', re.IGNORECASE)

HAS_ORIGIN = '(has origin)'


def iter_records(days: int) -> Iterator[dict]:
    """
    Yields the JSON log records of the last N days, reading the live
    access logs plus their logrotate rotations (.1 plain, .N.gz gzipped).
    Files older than the cutoff (mtime >= newest record) are skipped entirely,
    malformed lines are ignored.
    """

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    for path in sorted(LOGS_NGINX_DIR.glob('*access.jsonl*')):
        if datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc) < cutoff:
            continue

        opener = gzip.open if path.suffix == '.gz' else open
        try:
            with opener(path, 'rt', errors='replace') as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                        # $time_iso8601 carries the timezone offset
                        if datetime.fromisoformat(rec['time']) < cutoff:
                            continue
                    except (ValueError, KeyError, TypeError):
                        continue
                    yield rec
        except OSError as e:
            print(f'  lecture impossible: {path} ({e})')


def origin_of(rec: dict) -> str:
    """
    Client attribution without IP: host of the Origin header when present,
    falling back to the Referer host, then to the user-agent ('(ua) ...').
    Unparseable non-empty Origin values ('null', extension schemes) are kept
    as-is so they show up in the stats.
    """

    # nginx logs missing headers as '-'
    origin = rec.get('http_origin', '')
    if origin and origin != '-':
        m = HOST_RE.match(origin)
        return m.group('h').lower() if m else origin

    referer = rec.get('http_referrer', '')
    if referer and referer != '-':
        m = HOST_RE.match(referer)
        if m:
            return m.group('h').lower()

    return None


def ua_of(rec: dict, origin: str | None = None) -> str:
    """
    Returns a short user-agent string for stats, or '(none)' if missing.
    """
    if origin is not None:
        return HAS_ORIGIN

    ua = rec.get('http_user_agent', '')
    if ua and ua == HEALTHCHECK_USER_AGENT:
        return HAS_ORIGIN  # Skip this UA in the stats, it's just the healthcheck cron

    if ua and ua != '-':
        return f'(ua) {ua}'
    return '(none)'


def aggregate(days: int) -> dict:
    def empty():
        return {'requests': 0, 'bytes': 0, 'blocked': 0, 'statuses': Counter()}

    total = empty()
    origins: dict[str, dict] = {}
    uas: dict[str, dict] = {}
    datasets: Counter = Counter()

    for rec in iter_records(days):
        # self-traffic of the healthcheck cron, not a real consumer
        if rec.get('http_user_agent') == HEALTHCHECK_UA:
            continue

        origin = origin_of(rec) or '(none)'
        ua = ua_of(rec, origin=origin)
        buckets = [total, origins.setdefault(origin, empty()), uas.setdefault(ua, empty())]
        for b in buckets:
            b['requests'] += 1
            b['bytes'] += rec.get('body_bytes_sent', 0)
            b['blocked'] += rec.get('blocked', 0)
            b['statuses'][rec.get('status', 0)] += 1
        datasets[rec.get('dataset') or '(root)'] += 1

    uas.pop(HAS_ORIGIN, None)  # don't report uas for which we have an origin

    return {'days': days, 'total': total, 'origins': origins, 'uas': uas, 'datasets': datasets}


def _top_origins(agg: dict, top: int) -> list[tuple[str, dict]]:
    return sorted(agg['origins'].items(), key=lambda kv: -kv[1]['requests'])[:top]


def _top_uas(agg: dict, top: int) -> list[tuple[str, dict]]:
    return sorted(agg['uas'].items(), key=lambda kv: -kv[1]['requests'])[:top]


def _fmt_statuses(statuses: Counter) -> str:
    return ' '.join(f'{s}:{n}' for s, n in sorted(statuses.items()))


def _gb(n: int) -> str:
    return f'{n / 1e9:.2f}'


def format_text(agg: dict, top: int = 25) -> str:
    total = agg['total']
    lines = [
        f'Fenetre: {agg["days"]} jour(s)',
        f'Total: {total["requests"]} req, {_gb(total["bytes"])} GB, '
        f'{total["blocked"]} bloquees, statuts [{_fmt_statuses(total["statuses"])}]',
        '',
        f'{"origine":<45} {"req":>10} {"GB":>8} {"bloquees":>9}  statuts',
    ]
    for origin, s in _top_origins(agg, top):
        lines.append(
            f'{origin:<45} {s["requests"]:>10} {_gb(s["bytes"]):>8} {s["blocked"]:>9}'
            f'  [{_fmt_statuses(s["statuses"])}]'
        )
    if len(agg['origins']) > top:
        lines.append(f'... et {len(agg["origins"]) - top} autres origines')

    lines += ['', 'Par dataset:']
    for dataset, n in agg['datasets'].most_common():
        lines.append(f'  {dataset:<30} {n:>10}')

    return '\n'.join(lines)


def format_slack(agg: dict, top: int = 15, top_ua: int = 6) -> str:
    domain = config.ofm_config.get('domain_direct') or 'http-host'
    total = agg['total']

    lines = [
        f':bar_chart: *[{domain}] stats origines des {agg["days"]} derniers jours*',
        f'Total : {total["requests"]} req, {_gb(total["bytes"])} GB envoyes, '
        f'{total["blocked"]} bloquees',
        '',
        f'*Top {top} origines* (req / GB / % bloquees) :',
    ]
    for origin, s in _top_origins(agg, top):
        blocked_pct = 100 * s['blocked'] / s['requests'] if s['requests'] else 0
        lines.append(
            f'- `{origin}` — {s["requests"]} req, {_gb(s["bytes"])} GB, {blocked_pct:.0f}% bloquees'
        )
    if not agg['origins']:
        lines.append('- (aucune requete sur la fenetre)')

    lines += ['', f'*Top {top_ua} user-agents* sans origin (req / GB / % bloquees) :']
    for ua, s in _top_uas(agg, top_ua):
        blocked_pct = 100 * s['blocked'] / s['requests'] if s['requests'] else 0
        lines.append(f'- `{ua}`')
        lines.append(f'  ↳ {s["requests"]} req, {_gb(s["bytes"])} GB, {blocked_pct:.0f}% bloquees')
    if not agg['uas']:
        lines.append('- (aucune requete sur la fenetre)')

    # if agg['datasets']:
    #     top_datasets = ', '.join(f'{d} ({n})' for d, n in agg['datasets'].most_common(8))
    #     lines += ['', f'*Datasets* : {top_datasets}']

    blocked = read_blocklist()
    if blocked:
        lines += [
            '',
            f'*Blocklist courante* : {", ".join(f"`{d}`" for d in blocked)}',
        ]

    return '\n'.join(lines)


def run_stats_report() -> int:
    """
    Weekly cron entry point: aggregates the last 7 days and posts to Slack.
    Returns 0 on success, 1 if the Slack message could not be sent
    (send_slack prints the message when Slack is not configured).
    """

    agg = aggregate(7)
    sent = send_slack(format_slack(agg))
    return 0 if sent else 1
