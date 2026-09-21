#!/usr/bin/env python3
"""Generate one Grafana dashboard per service into dashboards/services/.

Hand-tuned dashboards (minecraft, jmusicbot, pantry-bot usage, host/node,
logs, overview, site-service-health) are NOT generated here -- they carry
application-specific panels that a template cannot express. This generator
covers the services that previously had no dashboard of their own, or that
were only visible inside a shared multi-service dashboard.

Every generated dashboard states the SITE the service runs at, because the
same service name can exist at more than one site during a failover.
"""
import json
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, 'dashboards', 'services')

PROM = {'type': 'prometheus', 'uid': '${DS_PROMETHEUS}'}
LOKI = {'type': 'loki', 'uid': '${DS_LOKI}'}

# site -> how the site is reached / what it means, shown on every dashboard.
SITES = {
    'home': 'MinecraftMachine — the single-node k3s cluster on the home LAN '
            '(192.168.40.208). Instrumented by kube-state-metrics, cAdvisor and Alloy/Loki.',
    'oracle': 'Oracle Cloud — remote failover site.',
    'canada': 'Canada — remote last-resort recovery site.',
}

# Each service: the workloads that make it up, and how to select them.
SERVICES = [
    dict(slug='authentik', name='Authentik (SSO)', site='home', ns='auth',
         workloads=['auth-authentik-server', 'auth-authentik-worker'],
         note='Authentik server and worker. The LDAP outpost and the Postgres '
              'backing store have their own dashboards.'),
    dict(slug='authentik-ldap', name='Authentik LDAP Outpost', site='home', ns='auth',
         workloads=['ldap-outpost'],
         note='LDAP outpost that fronts Authentik for LDAP clients.'),
    dict(slug='auth-postgres', name='Authentik Postgres', site='home', ns='auth',
         workloads=['auth-postgresql'], statefulset=True,
         note='Postgres backing store for Authentik. Matches every '
              'auth-postgresql* StatefulSet variant (primary, standby, return) '
              'so the dashboard follows whichever one is currently serving. No '
              'postgres_exporter is scraped, so these are container-level '
              'signals only.'),
    dict(slug='cartwise', name='Cartwise', site='home', ns='cartwise',
         workloads=['cartwise'],
         note='Cartwise web application. Its database has a separate dashboard.'),
    dict(slug='cartwise-db', name='Cartwise Database', site='home', ns='cartwise',
         workloads=['cartwise-db'],
         note='Cartwise database container. Container-level signals only — no '
              'database exporter is scraped.'),
    dict(slug='ci-tunnel', name='CI Tunnel (cloudflared)', site='home', ns='ci-tunnel',
         workloads=['cloudflared'],
         note='cloudflared connector dedicated to CI access. Separate from the '
              'pantry-bot tunnels on the Cloudflare Tunnel dashboard.'),
    dict(slug='traefik', name='Traefik Ingress', site='home', ns='kube-system',
         workloads=['traefik'],
         note='k3s bundled Traefik ingress controller.'),
    dict(slug='coredns', name='CoreDNS', site='home', ns='kube-system',
         workloads=['coredns'],
         note='Cluster DNS. A CoreDNS failure shows up as name-resolution '
              'errors in every other service before it shows up here.'),
    dict(slug='operations-web', name='Operations Web', site='home', ns='operations',
         workloads=['operations-web'],
         note='Operations web front end.'),
    dict(slug='opsbot', name='Opsbot', site='home', ns='opsbot',
         workloads=['opsbot'],
         note='Operations bot.'),
    dict(slug='prometheus', name='Prometheus', site='home', ns='observability',
         workloads=['prometheus'],
         note='The Prometheus that backs every other dashboard here. If this is '
              'down, every other panel is stale rather than healthy.'),
    dict(slug='loki', name='Loki', site='home', ns='observability',
         workloads=['loki'],
         note='Log store behind every logs panel.'),
    dict(slug='grafana', name='Grafana', site='home', ns='observability',
         workloads=['grafana'],
         note='This Grafana itself.'),
    dict(slug='alloy-logs', name='Alloy (log shipper)', site='home', ns='observability',
         workloads=['alloy-logs'], daemonset=True,
         note='Grafana Alloy DaemonSet: ships pod logs to Loki and receives '
              "remote-write metrics from Greenie's Windows PC."),
    dict(slug='kube-state-metrics', name='kube-state-metrics', site='home', ns='observability',
         workloads=['kube-state-metrics'],
         note='Exports Kubernetes object state. Every kube_* panel in this '
              'Grafana depends on it.'),
    dict(slug='mcp-grafana', name='mcp-grafana', site='home', ns='observability',
         workloads=['mcp-grafana', 'mcp-grafana-cloudflared'],
         note='MCP server exposing this Grafana to agents, plus its cloudflared '
              'connector.'),
    dict(slug='failover-witness', name='Failover Witness Relay', site='home', ns='observability',
         workloads=['failover-witness-relay'], daemonset=True,
         note='Relay for the multi-site fencing lease. Its own /metrics '
              '(failover_witness_lease_active) is not scraped by this Prometheus '
              'yet, so lease ownership is not answerable here.'),
    # pantry-bot microservices: each gets its own dashboard rather than sharing one.
    dict(slug='pantry-private-api', name='PantryBot API', site='home', ns='pantry-bot',
         workloads=['pantry-private-api'], job='pantry-bot-api',
         note='PantryBot private API. Exposes /metrics (job pantry-bot-api).'),
    dict(slug='pantry-twitch-gateway', name='PantryBot Twitch Gateway', site='home', ns='pantry-bot',
         workloads=['pantry-twitch-gateway'], job='pantry-bot-gateway',
         note='Twitch gateway. Scraped as job pantry-bot-gateway when the home '
              'app plane holds the Twitch leases.'),
    dict(slug='pantry-twitch-dispatcher', name='PantryBot Twitch Dispatcher', site='home', ns='pantry-bot',
         workloads=['pantry-twitch-dispatcher'], job='pantry-bot-dispatcher',
         note='Twitch dispatcher. Scraped as job pantry-bot-dispatcher when the '
              'home app plane holds the Twitch leases.'),
    dict(slug='pantry-chat-worker', name='PantryBot Chat Worker', site='home', ns='pantry-bot',
         workloads=['pantry-chat-worker'], job='pantry-bot-worker',
         note='Chat worker. Scraped as job pantry-bot-worker when the home app '
              'plane holds the Twitch leases.'),
    dict(slug='pantry-commands-site', name='PantryBot Commands Site', site='home', ns='pantry-bot',
         workloads=['pantry-commands-site', 'commands-cloudflared'],
         note='Public commands site and its cloudflared connector.'),
    dict(slug='pantry-private-site', name='PantryBot Private Site', site='home', ns='pantry-bot',
         workloads=['pantry-private-site'],
         note='Private (authenticated) PantryBot site.'),
    dict(slug='pantry-overlay-delivery', name='PantryBot Overlay Delivery', site='home', ns='pantry-bot',
         workloads=['pantry-overlay-delivery'],
         note='Stream overlay delivery service.'),
    dict(slug='pantry-postgres-authority', name='PantryBot Postgres Authority', site='home', ns='pantry-bot',
         workloads=['postgres-authority'], statefulset=True,
         note='PantryBot Postgres authority. Matches every postgres-authority* '
              'StatefulSet variant (authority, home, home-v2, home-failback, '
              'home-return) so the dashboard follows whichever one is currently '
              'serving rather than going dark after a failover. '
              'Application-level reads from these tables are on the '
              'PantryBot — Usage (Postgres) dashboard.'),
]


_RE2_SPECIAL = re.compile(r'([.+*?()|\[\]{}^$\\])')


def rx_quote(s):
    """Escape for Go RE2. Unlike re.escape, never emits \\- (RE2 rejects it)."""
    return _RE2_SPECIAL.sub(r'\\\1', s)


def pod_re(workloads):
    """Regex matching every pod of the given workloads (ReplicaSet/StatefulSet suffixes)."""
    return '|'.join('%s-.*' % rx_quote(w) for w in workloads)


def wl_re(workloads):
    return '|'.join(rx_quote(w) for w in workloads)


def _t(expr, legend=None, ref='A', instant=False, fmt=None):
    t = {'refId': ref, 'expr': expr, 'datasource': PROM}
    if legend is not None:
        t['legendFormat'] = legend
    if instant:
        t['instant'] = True
    if fmt:
        t['format'] = fmt
    return t


def _panel(pid, ptype, title, gp, targets, **kw):
    p = {'id': pid, 'type': ptype, 'title': title, 'gridPos': gp,
         'datasource': PROM, 'targets': targets}
    p.update(kw)
    return p


def build(svc):
    ns = svc['ns']
    wls = svc['workloads']
    pods = pod_re(wls)
    site = svc['site']
    sel = 'namespace="%s", pod=~"%s"' % (ns, pods)
    ksel = 'namespace="%s", pod=~"%s"' % (ns, pods)
    panels = []
    pid = [0]

    def nid():
        pid[0] += 1
        return pid[0]

    y = [0]

    def row(title):
        panels.append({'id': nid(), 'type': 'row', 'title': title, 'collapsed': False,
                       'gridPos': {'h': 1, 'w': 24, 'x': 0, 'y': y[0]}, 'panels': []})
        y[0] += 1

    kind = ('StatefulSet' if svc.get('statefulset') else
            'DaemonSet' if svc.get('daemonset') else 'Deployment')
    header = (
        '## %s\n\n'
        '**Site: `%s`** — %s\n\n'
        '**Namespace:** `%s`  |  **%s(s):** %s\n\n%s'
        % (svc['name'], site, SITES[site], ns, kind,
           ', '.join('`%s`' % w for w in wls), svc['note'])
    )
    row('%s — site: %s' % (svc['name'], site))
    panels.append({'id': nid(), 'type': 'text', 'title': 'Service and site',
                   'gridPos': {'h': 4, 'w': 24, 'x': 0, 'y': y[0]},
                   'options': {'mode': 'markdown', 'content': header}})
    y[0] += 4

    # --- status stats -------------------------------------------------
    row('Health')
    stats = [
        ('Site', 'vector(1)', 'stat', {'reduceOptions': {'calcs': ['lastNotNull']}},
         site),
        ('Pods Running',
         'sum(kube_pod_status_phase{%s, phase="Running"}) or vector(0)' % ksel, 'stat', {}, None),
        ('Containers Ready',
         'sum(kube_pod_container_status_ready{%s}) or vector(0)' % ksel, 'stat', {}, None),
        ('Restarts (24h)',
         'sum(increase(kube_pod_container_status_restarts_total{%s}[24h])) or vector(0)' % ksel,
         'stat', {}, None),
    ]
    x = 0
    for title, expr, ptype, extra, txt in stats:
        opts = {'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '', 'values': False},
                'colorMode': 'value', 'graphMode': 'none', 'textMode': 'auto'}
        fc = {'defaults': {'unit': 'short', 'mappings': [],
                           'color': {'mode': 'thresholds'},
                           'thresholds': {'mode': 'absolute',
                                          'steps': [{'color': 'text', 'value': None}]}},
              'overrides': []}
        if title == 'Site':
            fc['defaults']['mappings'] = [{'type': 'value',
                                           'options': {'1': {'text': site, 'index': 0}}}]
            fc['defaults']['color'] = {'mode': 'fixed', 'fixedColor': 'blue'}
        if title == 'Restarts (24h)':
            fc['defaults']['thresholds']['steps'] = [
                {'color': 'green', 'value': None},
                {'color': 'orange', 'value': 1},
                {'color': 'red', 'value': 5}]
        p = _panel(nid(), 'stat', title, {'h': 4, 'w': 6, 'x': x, 'y': y[0]},
                   [_t(expr, instant=True)], options=opts, fieldConfig=fc)
        panels.append(p)
        x += 6
    y[0] += 4

    panels.append(_panel(
        nid(), 'table', 'Pod status', {'h': 8, 'w': 24, 'x': 0, 'y': y[0]},
        [_t('kube_pod_status_phase{%s} == 1' % ksel, '', 'A', instant=True, fmt='table'),
         _t('sum by (pod) (kube_pod_container_status_restarts_total{%s})' % ksel,
            '', 'B', instant=True, fmt='table'),
         _t('time() - max by (pod) (kube_pod_start_time{%s})' % ksel,
            '', 'C', instant=True, fmt='table')],
        transformations=[
            {'id': 'joinByField', 'options': {'byField': 'pod', 'mode': 'outer'}},
            {'id': 'organize', 'options': {'excludeByName': {
                'Time': True, 'Time 1': True, 'Time 2': True, 'Time 3': True,
                '__name__': True, 'job': True, 'instance': True,
                'uid': True, 'container': True, 'endpoint': True, 'service': True},
                'renameByName': {'Value #A': 'Phase', 'Value #B': 'Restarts',
                                 'Value #C': 'Age (s)', 'phase': 'Phase',
                                 'namespace': 'Namespace', 'pod': 'Pod'}}}],
        fieldConfig={'defaults': {'custom': {}}, 'overrides': [
            {'matcher': {'id': 'byName', 'options': 'Age (s)'},
             'properties': [{'id': 'unit', 'value': 's'}]}]},
        options={'showHeader': True}))
    y[0] += 8

    # --- resources ------------------------------------------------------
    row('Resources')
    panels.append(_panel(
        nid(), 'timeseries', 'Memory working set vs limit',
        {'h': 8, 'w': 12, 'x': 0, 'y': y[0]},
        [_t('sum by (pod) (container_memory_working_set_bytes{%s, container!=""})' % sel,
            '{{pod}}'),
         _t('sum by (pod) (kube_pod_container_resource_limits{%s, resource="memory"})' % ksel,
            'limit — {{pod}}', 'B')],
        fieldConfig={'defaults': {'unit': 'bytes', 'custom': {'fillOpacity': 8,
                                                              'lineWidth': 1}},
                     'overrides': [{'matcher': {'id': 'byRegexp', 'options': 'limit.*'},
                                    'properties': [{'id': 'custom.lineStyle',
                                                    'value': {'dash': [8, 8], 'fill': 'dash'}},
                                                   {'id': 'custom.fillOpacity', 'value': 0}]}]},
        options={'legend': {'displayMode': 'table', 'placement': 'bottom',
                            'calcs': ['lastNotNull', 'max']},
                 'tooltip': {'mode': 'multi', 'sort': 'desc'}}))
    panels.append(_panel(
        nid(), 'timeseries', 'CPU usage vs limit (cores)',
        {'h': 8, 'w': 12, 'x': 12, 'y': y[0]},
        [_t('sum by (pod) (rate(container_cpu_usage_seconds_total{%s, container!=""}[$__rate_interval]))' % sel,
            '{{pod}}'),
         _t('sum by (pod) (kube_pod_container_resource_limits{%s, resource="cpu"})' % ksel,
            'limit — {{pod}}', 'B')],
        fieldConfig={'defaults': {'unit': 'none', 'decimals': 3,
                                  'custom': {'fillOpacity': 8, 'lineWidth': 1}},
                     'overrides': [{'matcher': {'id': 'byRegexp', 'options': 'limit.*'},
                                    'properties': [{'id': 'custom.lineStyle',
                                                    'value': {'dash': [8, 8], 'fill': 'dash'}},
                                                   {'id': 'custom.fillOpacity', 'value': 0}]}]},
        options={'legend': {'displayMode': 'table', 'placement': 'bottom',
                            'calcs': ['lastNotNull', 'max']},
                 'tooltip': {'mode': 'multi', 'sort': 'desc'}}))
    y[0] += 8

    panels.append(_panel(
        nid(), 'timeseries', 'Container network I/O',
        {'h': 8, 'w': 12, 'x': 0, 'y': y[0]},
        [_t('sum by (pod) (rate(container_network_receive_bytes_total{%s}[$__rate_interval]))' % sel,
            'rx — {{pod}}'),
         _t('sum by (pod) (rate(container_network_transmit_bytes_total{%s}[$__rate_interval]))' % sel,
            'tx — {{pod}}', 'B')],
        fieldConfig={'defaults': {'unit': 'Bps', 'custom': {'fillOpacity': 8}},
                     'overrides': []},
        options={'legend': {'displayMode': 'list', 'placement': 'bottom'},
                 'tooltip': {'mode': 'multi', 'sort': 'desc'}}))
    panels.append(_panel(
        nid(), 'timeseries', 'CPU throttling (% of periods)',
        {'h': 8, 'w': 12, 'x': 12, 'y': y[0]},
        [_t('100 * sum by (pod) (rate(container_cpu_cfs_throttled_periods_total{%s}[$__rate_interval]))'
            ' / clamp_min(sum by (pod) (rate(container_cpu_cfs_periods_total{%s}[$__rate_interval])), 1)'
            % (sel, sel), '{{pod}}')],
        fieldConfig={'defaults': {'unit': 'percent', 'min': 0,
                                  'custom': {'fillOpacity': 8}}, 'overrides': []},
        options={'legend': {'displayMode': 'list', 'placement': 'bottom'},
                 'tooltip': {'mode': 'multi', 'sort': 'desc'}}))
    y[0] += 8

    # --- scrape target (only when the service exposes /metrics) ----------
    if svc.get('job'):
        row('Application scrape target')
        panels.append(_panel(
            nid(), 'timeseries', 'Scrape target up (job %s)' % svc['job'],
            {'h': 6, 'w': 24, 'x': 0, 'y': y[0]},
            [_t('up{job="%s"}' % svc['job'], '{{instance}} (site {{site}})')],
            description='0 means this Prometheus could not scrape the service. '
                        'During a failover the home instance is expected to be '
                        'down while another site holds the lease.',
            fieldConfig={'defaults': {'unit': 'short', 'min': 0, 'max': 1,
                                      'custom': {'fillOpacity': 20,
                                                 'lineInterpolation': 'stepAfter'}},
                         'overrides': []},
            options={'legend': {'displayMode': 'list', 'placement': 'bottom'},
                     'tooltip': {'mode': 'multi'}}))
        y[0] += 6

    # --- logs ------------------------------------------------------------
    row('Logs')
    stream = '{instance=~"%s/(%s)-.*"}' % (ns, '|'.join(rx_quote(w) for w in wls))
    panels.append({
        'id': nid(), 'type': 'timeseries', 'title': 'Log volume by level',
        'gridPos': {'h': 7, 'w': 24, 'x': 0, 'y': y[0]}, 'datasource': LOKI,
        'targets': [{'refId': 'A', 'datasource': LOKI,
                     'expr': 'sum by (level) (count_over_time(%s [$__auto]))' % stream,
                     'legendFormat': '{{level}}'}],
        'fieldConfig': {'defaults': {'custom': {'fillOpacity': 40, 'stacking':
                                                {'mode': 'normal', 'group': 'A'},
                                                'drawStyle': 'bars', 'lineWidth': 0}},
                        'overrides': [
                            {'matcher': {'id': 'byName', 'options': 'error'},
                             'properties': [{'id': 'color', 'value': {'mode': 'fixed',
                                                                      'fixedColor': 'red'}}]},
                            {'matcher': {'id': 'byName', 'options': 'warn'},
                             'properties': [{'id': 'color', 'value': {'mode': 'fixed',
                                                                      'fixedColor': 'orange'}}]}]},
        'options': {'legend': {'displayMode': 'list', 'placement': 'bottom'},
                    'tooltip': {'mode': 'multi', 'sort': 'desc'}}})
    y[0] += 7
    panels.append({
        'id': nid(), 'type': 'logs', 'title': 'Logs',
        'gridPos': {'h': 12, 'w': 24, 'x': 0, 'y': y[0]}, 'datasource': LOKI,
        'targets': [{'refId': 'A', 'datasource': LOKI, 'expr': stream}],
        'options': {'showTime': True, 'wrapLogMessage': True, 'sortOrder': 'Descending',
                    'enableLogDetails': True}})
    y[0] += 12

    return {
        'uid': 'svc-%s' % svc['slug'],
        'title': '%s — Service Health' % svc['name'],
        'description': '%s Site: %s. Namespace: %s. Generated by '
                       'scripts/gen-service-dashboards.py — edit the registry in '
                       'that script, not this file.' % (svc['note'], site, ns),
        'tags': ['service', 'site:%s' % site, 'ns:%s' % ns, 'generated'],
        'timezone': 'browser',
        'editable': True,
        'schemaVersion': 39,
        'version': 1,
        'refresh': '1m',
        'graphTooltip': 1,
        'time': {'from': 'now-6h', 'to': 'now'},
        'links': [{'title': 'All service dashboards', 'type': 'dashboards',
                   'tags': ['service'], 'asDropdown': True, 'icon': 'external link',
                   'includeVars': False, 'keepTime': True, 'targetBlank': False,
                   'tooltip': '', 'url': ''},
                  {'title': 'Site & Service Health', 'type': 'link',
                   'url': '/d/homelab-site-service-health', 'icon': 'dashboard',
                   'asDropdown': False, 'keepTime': True, 'includeVars': False,
                   'tags': [], 'targetBlank': False, 'tooltip': ''}],
        'templating': {'list': [
            {'name': 'DS_PROMETHEUS', 'type': 'datasource', 'query': 'prometheus',
             'current': {'text': 'Prometheus', 'value': 'Prometheus'}, 'hide': 0,
             'label': 'Prometheus', 'refresh': 1, 'options': [], 'regex': ''},
            {'name': 'DS_LOKI', 'type': 'datasource', 'query': 'loki',
             'current': {'text': 'Loki', 'value': 'Loki'}, 'hide': 0,
             'label': 'Loki', 'refresh': 1, 'options': [], 'regex': ''}]},
        'panels': panels,
    }


def main():
    os.makedirs(OUT, exist_ok=True)
    written = []
    for svc in SERVICES:
        d = build(svc)
        path = os.path.join(OUT, '%s.json' % svc['slug'])
        with open(path, 'w') as fh:
            json.dump(d, fh, indent=1)
            fh.write('\n')
        written.append(os.path.relpath(path, REPO))
    for w in written:
        print(w)
    print('%d service dashboards written' % len(written))


if __name__ == '__main__':
    main()
