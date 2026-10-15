from ssh_lib import ASSETS_DIR, dotenv_val
from ssh_lib.utils import put, put_str, sudo_cmd

OTELCOL_VERSION = '0.162.0'
OTELCOL_CONF_DIR = '/etc/otelcol-contrib'


def otelcol(c):
    """
    OpenTelemetry Collector (contrib) pushing the host metrics to a Prometheus
    remote_write endpoint. Without PROMETHEUS_REMOTE_WRITE_URL/PROMETHEUS_PUSH_TOKEN in
    .env, the service is stopped and disabled if a previous deploy installed it.
    """
    url = dotenv_val('PROMETHEUS_REMOTE_WRITE_URL')
    token = dotenv_val('PROMETHEUS_PUSH_TOKEN')

    if not (url and token):
        c.sudo('systemctl disable --now otelcol-contrib', warn=True, hide=True)
        return

    installed = c.sudo(
        "dpkg-query -W -f='${Version}' otelcol-contrib", warn=True, hide=True
    ).stdout.strip()
    if installed != OTELCOL_VERSION:
        deb = f'otelcol-contrib_{OTELCOL_VERSION}_linux_$(dpkg --print-architecture).deb'
        sudo_cmd(
            c,
            'cd /tmp && '
            f'curl -fsSLO https://github.com/open-telemetry/opentelemetry-collector-releases'
            f'/releases/download/v{OTELCOL_VERSION}/{deb} && '
            f'DEBIAN_FRONTEND=noninteractive dpkg -i {deb}; '
            f'rm -f /tmp/{deb}',
        )

    put(
        c,
        ASSETS_DIR / 'otelcol' / 'config.yaml',
        f'{OTELCOL_CONF_DIR}/config.yaml',
        permissions='644',
    )

    # read by systemd (root) only, the token must not be world-readable
    put_str(
        c,
        f'{OTELCOL_CONF_DIR}/otelcol-contrib.conf',
        f'OTELCOL_OPTIONS="--config={OTELCOL_CONF_DIR}/config.yaml"\n'
        f"PROMETHEUS_REMOTE_WRITE_URL='{url}'\n"
        f"PROMETHEUS_PUSH_TOKEN='{token}'",
    )
    c.sudo(f'chmod 600 {OTELCOL_CONF_DIR}/otelcol-contrib.conf')

    c.sudo('systemctl enable otelcol-contrib')
    c.sudo('systemctl restart otelcol-contrib')
