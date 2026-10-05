import gzip
import json
import re
from array import array
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Iterator

from http_host_lib.blocklist import read_blocklist
from http_host_lib.config import config
from http_host_lib.healthcheck import HEALTHCHECK_USER_AGENT, send_slack


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
    if ua and ua != '-':
        return ua
    return '(none)'


def aggregate(days: int) -> dict:
    def empty():
        return {'requests': 0, 'bytes': 0, 'blocked': 0, 'statuses': Counter()}

    total = empty()
    origins: dict[str, dict] = {}
    uas: dict[str, dict] = {}
    datasets: Counter = Counter()
    # request_time in seconds; array('f') keeps millions of samples compact
    latency_total = array('f')
    latency_datasets: dict[str, array] = {}

    for rec in iter_records(days):
        # self-traffic of the healthcheck cron, not a real consumer
        if rec.get('http_user_agent') == HEALTHCHECK_USER_AGENT:
            continue

        origin = origin_of(rec)
        ua = ua_of(rec, origin=origin)
        buckets = [total, origins.setdefault(origin, empty()), uas.setdefault(ua, empty())]
        for b in buckets:
            b['requests'] += 1
            b['bytes'] += rec.get('body_bytes_sent', 0)
            b['blocked'] += rec.get('blocked', 0)
            b['statuses'][rec.get('status', 0)] += 1
        dataset = rec.get('dataset') or '(root)'
        datasets[dataset] += 1

        # blocked 403s are answered instantly and would skew the latencies;
        # records from before request_time was logged have no value
        rt = rec.get('request_time')
        if isinstance(rt, (int, float)) and not rec.get('blocked'):
            latency_total.append(rt)
            latency_datasets.setdefault(dataset, array('f')).append(rt)

    uas.pop(HAS_ORIGIN, None)  # don't report uas for which we have an origin

    return {
        'days': days,
        'total': total,
        'origins': origins,
        'uas': uas,
        'datasets': datasets,
        'latency_total': latency_total,
        'latency_datasets': latency_datasets,
    }


def percentile(sorted_values, p: float) -> float:
    """
    Nearest-rank percentile of an already sorted sequence (exact, no interpolation).
    """

    rank = max(1, -(-len(sorted_values) * p // 100))  # ceil
    return sorted_values[int(rank) - 1]


def _latency_row(label: str, values) -> str:
    v = sorted(values)
    ms = [1000 * sum(v) / len(v)] + [1000 * percentile(v, p) for p in (50, 95, 99)]
    return f'{label:<24} {len(v):>10} {ms[0]:>7.0f} {ms[1]:>7.0f} {ms[2]:>7.0f} {ms[3]:>7.0f}'


def _latency_table(agg: dict, top: int = 10) -> list[str]:
    """
    Fixed-width table (global + per dataset) of request_time in ms,
    meant to be shown in a monospace block. Empty if no request_time was logged.
    """

    if not len(agg['latency_total']):
        return []

    lines = [f'{"":<24} {"req":>10} {"moy ms":>7} {"p50":>7} {"p95":>7} {"p99":>7}']
    lines.append(_latency_row('TOTAL', agg['latency_total']))
    by_count = sorted(agg['latency_datasets'].items(), key=lambda kv: -len(kv[1]))
    for dataset, values in by_count[:top]:
        lines.append(_latency_row(dataset[:24], values))
    if len(by_count) > top:
        lines.append(f'... et {len(by_count) - top} autres datasets')
    return lines


NO_ORIGIN = '(sans origin)'


def _top_origins(agg: dict, top: int) -> list[tuple[str, dict]]:
    # requests without Origin nor Referer are bucketed under the None key
    items = [(o or NO_ORIGIN, s) for o, s in agg['origins'].items()]
    return sorted(items, key=lambda kv: -kv[1]['requests'])[:top]


def _top_uas(agg: dict, top: int) -> list[tuple[str, dict]]:
    return sorted(agg['uas'].items(), key=lambda kv: -kv[1]['requests'])[:top]


def _gb(n: int) -> str:
    return f'{n / 1e9:.2f}'


ORIGIN_STATUSES = (200, 304, 401, 404, 499)


def _origin_row(label: str, s: dict, name_width: int) -> str:
    if len(label) > name_width:
        label = label[: name_width - 1] + '…'
    blocked_pct = 100 * s['blocked'] / s['requests'] if s['requests'] else 0
    others = s['requests'] - sum(s['statuses'][st] for st in ORIGIN_STATUSES)
    return (
        f'{label:<{name_width}} {s["requests"]:>9} {_gb(s["bytes"]):>7} {blocked_pct:>5.0f}%'
        + ''.join(f' {s["statuses"][st]:>6}' for st in ORIGIN_STATUSES)
        + f' {others:>6}'
    )


def _origins_table(agg: dict, top: int, name_width: int) -> list[str]:
    """
    Fixed-width table: a TOTAL row (all origins) then the top origins (hostname,
    requests, GB, % blocked, a column per common status, the rest in 'autres'),
    meant for a monospace block.
    """

    lines = [
        f'{"hostname":<{name_width}} {"req":>9} {"GB":>7} {"%bloq":>6}'
        + ''.join(f' {st:>6}' for st in ORIGIN_STATUSES)
        + f' {"autres":>6}',
        _origin_row('TOTAL', agg['total'], name_width),
    ]
    lines += [_origin_row(origin, s, name_width) for origin, s in _top_origins(agg, top)]
    if len(agg['origins']) > top:
        lines.append(f'... et {len(agg["origins"]) - top} autres origines')
    return lines


def _uas_table(agg: dict, top: int, ua_width: int = 200) -> list[str]:
    """
    Fixed-width table of the top user-agents of requests without origin
    (requests, % blocked, user-agent; no volume), UA last so it can be long.
    """

    lines = [f'{"req":>9} {"%bloq":>6}  UA']
    for ua, s in _top_uas(agg, top):
        blocked_pct = 100 * s['blocked'] / s['requests'] if s['requests'] else 0
        if len(ua) > ua_width:
            ua = ua[: ua_width - 1] + '…'
        lines.append(f'{s["requests"]:>9} {blocked_pct:>5.0f}%  {ua}')
    if len(agg['uas']) > top:
        lines.append(f'... et {len(agg["uas"]) - top} autres user-agents')
    return lines


def format_text(agg: dict, top: int = 25) -> str:
    lines = [
        f'Fenetre: {agg["days"]} jour(s)',
        '',
        *_origins_table(agg, top, name_width=45),
    ]

    lines += ['', 'Par dataset:']
    for dataset, n in agg['datasets'].most_common():
        lines.append(f'  {dataset:<30} {n:>10}')

    latency = _latency_table(agg, top=len(agg['latency_datasets']))
    if latency:
        lines += ['', 'Temps de reponse (request_time, hors bloquees):', *latency]

    return '\n'.join(lines)


def format_slack(agg: dict, top: int = 15, top_ua: int = 6) -> str:
    domain = config.ofm_config.get('domain_direct') or 'http-host'

    lines = [f':bar_chart: *[{domain}] Stats des {agg["days"]} derniers jours*']

    lines += ['', f'*Top {top} origines* :']
    if agg['origins']:
        lines += ['```', *_origins_table(agg, top, name_width=30), '```']
    else:
        lines.append('- (aucune requete sur la fenetre)')

    latency = _latency_table(agg)
    if latency:
        lines += ['', '*Temps de reponse* :', '```', *latency, '```']

    lines += ['', f'*Top {top_ua} user-agents* sans origin :']
    if agg['uas']:
        lines += ['```', *_uas_table(agg, top_ua), '```']
    else:
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
