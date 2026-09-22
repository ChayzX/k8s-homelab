#!/usr/bin/env python3
"""Generate the homelab's Grafana dashboards.

The set is deliberately small. Every dashboard answers one plain question:

  start-here.json          Is anything broken right now?        (open this first)
  services.json            How is <one service> doing?           (pick from a list)
  machines.json            How is <one machine> doing?           (pick from a list)
  pantry-bot.json          Is PantryBot healthy, and is it used?
  site-service-health.json Which site is each thing running at?

logs.json and minecraft.json are hand-written and are not produced here.

Pickers are data-driven: the Services list is read from kube-state-metrics at
view time, so a new Deployment shows up without editing this file. Re-run
this script, then scripts/gen-dashboards-configmap.py, after any change.
"""
import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, 'dashboards')

P = {'type': 'prometheus', 'uid': '${DS_PROMETHEUS}'}
L = {'type': 'loki', 'uid': '${DS_LOKI}'}
PG = {'type': 'grafana-postgresql-datasource', 'uid': '${DS_POSTGRES}'}

GREEN, ORANGE, RED, BLUE, GREY = 'green', 'orange', 'red', 'blue', 'text'

DS_VARS = {
    'prom': {'name': 'DS_PROMETHEUS', 'label': 'Prometheus', 'type': 'datasource',
             'query': 'prometheus', 'current': {}, 'hide': 2, 'refresh': 1, 'regex': ''},
    'loki': {'name': 'DS_LOKI', 'label': 'Loki', 'type': 'datasource',
             'query': 'loki', 'current': {}, 'hide': 2, 'refresh': 1, 'regex': ''},
    'pg': {'name': 'DS_POSTGRES', 'label': 'Postgres', 'type': 'datasource',
           'query': 'grafana-postgresql-datasource', 'current': {}, 'hide': 2,
           'refresh': 1, 'regex': ''},
}

NAV = [
    {'title': 'Start Here', 'url': '/d/home', 'type': 'link', 'icon': 'dashboard'},
    {'title': 'Services', 'url': '/d/services', 'type': 'link', 'icon': 'apps'},
    {'title': 'Machines', 'url': '/d/machines', 'type': 'link', 'icon': 'monitor'},
    {'title': 'PantryBot', 'url': '/d/homelab-pantry-bot', 'type': 'link', 'icon': 'bolt'},
    {'title': 'Minecraft', 'url': '/d/homelab-minecraft', 'type': 'link', 'icon': 'cube'},
    {'title': 'Logs', 'url': '/d/homelab-logs', 'type': 'link', 'icon': 'doc'},
    {'title': 'Sites & Failover', 'url': '/d/homelab-site-service-health', 'type': 'link',
     'icon': 'sitemap'},
]


def nav_links():
    return [dict(l, asDropdown=False, includeVars=False, keepTime=True, targetBlank=False,
                 tags=[], tooltip='') for l in NAV]


class Board:
    def __init__(self):
        self.panels = []
        self.y = 0
        self._id = 0

    def nid(self):
        self._id += 1
        return self._id

    def row(self, title):
        self.panels.append({'type': 'row', 'title': title, 'collapsed': False,
                            'gridPos': {'h': 1, 'w': 24, 'x': 0, 'y': self.y},
                            'panels': [], 'id': self.nid()})
        self.y += 1

    def text(self, title, md, h=3, w=24, x=0, advance=True):
        self.panels.append({'id': self.nid(), 'type': 'text', 'title': title,
                            'gridPos': {'h': h, 'w': w, 'x': x, 'y': self.y},
                            'options': {'mode': 'markdown', 'content': md}})
        if advance:
            self.y += h

    def add(self, panel, h, w, x, advance):
        panel['id'] = self.nid()
        panel['gridPos'] = {'h': h, 'w': w, 'x': x, 'y': self.y}
        self.panels.append(panel)
        if advance:
            self.y += h


def t(expr, ref='A', legend=None, instant=False, fmt=None, ds=P):
    d = {'refId': ref, 'datasource': ds, 'expr': expr}
    if legend is not None:
        d['legendFormat'] = legend
    if instant:
        d['instant'] = True
        d['range'] = False
    if fmt:
        d['format'] = fmt
    return d


def steps(*pairs):
    return {'mode': 'absolute',
            'steps': [{'color': c, 'value': v} for v, c in pairs]}


def stat(title, targets, thresholds, desc='', mappings=None, unit='short',
         decimals=0, color_mode='background', text_mode='auto'):
    return {'type': 'stat', 'title': title, 'description': desc, 'datasource': P,
            'targets': targets,
            'options': {'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '',
                                          'values': False},
                        'colorMode': color_mode, 'graphMode': 'none',
                        'textMode': text_mode, 'justifyMode': 'center',
                        'orientation': 'auto'},
            'fieldConfig': {'defaults': {'unit': unit, 'decimals': decimals,
                                         'mappings': mappings or [],
                                         'noValue': 'no data',
                                         'thresholds': thresholds},
                            'overrides': []}}


def words(mapping):
    """Value -> (text, colour) mapping, so stats read as words, not numbers."""
    return [{'type': 'value', 'options': {
        str(k): {'text': v[0], 'color': v[1], 'index': i}
        for i, (k, v) in enumerate(mapping.items())}}]


def gauge(title, targets, desc='', warn=70, crit=90):
    return {'type': 'gauge', 'title': title, 'description': desc, 'datasource': P,
            'targets': targets,
            'options': {'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '',
                                          'values': False},
                        'showThresholdMarkers': True, 'showThresholdLabels': False},
            'fieldConfig': {'defaults': {'unit': 'percent', 'min': 0, 'max': 100,
                                         'decimals': 0, 'noValue': 'no data',
                                         'thresholds': steps((None, GREEN), (warn, ORANGE),
                                                             (crit, RED))},
                            'overrides': []}}


def ts(title, targets, unit='short', desc='', ds=P, stack=False, bars=False,
       minv=None, maxv=None, thresholds=None, legend='list'):
    custom = {'drawStyle': 'bars' if bars else 'line', 'lineWidth': 1 if bars else 2,
              'fillOpacity': 60 if bars else 12, 'showPoints': 'never',
              'spanNulls': True, 'gradientMode': 'opacity'}
    if stack:
        custom['stacking'] = {'mode': 'normal', 'group': 'A'}
    if thresholds:
        custom['thresholdsStyle'] = {'mode': 'line+area'}
    defaults = {'unit': unit, 'custom': custom, 'color': {'mode': 'palette-classic'},
                'noValue': 'nothing to show'}
    if minv is not None:
        defaults['min'] = minv
    if maxv is not None:
        defaults['max'] = maxv
    if thresholds:
        defaults['thresholds'] = thresholds
    return {'type': 'timeseries', 'title': title, 'description': desc, 'datasource': ds,
            'targets': targets,
            'options': {'legend': {'displayMode': legend, 'placement': 'bottom',
                                   'showLegend': True},
                        'tooltip': {'mode': 'multi', 'sort': 'desc'}},
            'fieldConfig': {'defaults': defaults, 'overrides': []}}


def table(title, targets, rename, hide=(), desc='', overrides=None, sort=None,
          no_value='Nothing here — good.'):
    tr = [{'id': 'merge', 'options': {}},
          {'id': 'organize', 'options': {
              'excludeByName': dict({'Time': True, '__name__': True, 'job': True,
                                     'instance': True, 'uid': True, 'endpoint': True,
                                     'service': True},
                                    **{h: True for h in hide}),
              'renameByName': rename,
              'indexByName': {k: i for i, k in enumerate(rename)}}}]
    if sort:
        tr.append({'id': 'sortBy', 'options': {'fields': {}, 'sort': [{'field': sort}]}})
    return {'type': 'table', 'title': title, 'description': desc, 'datasource': P,
            'targets': targets, 'transformations': tr,
            'options': {'showHeader': True, 'cellHeight': 'sm',
                        'footer': {'show': False, 'reducer': ['sum'], 'fields': ''}},
            'fieldConfig': {'defaults': {'custom': {'align': 'auto',
                                                    'cellOptions': {'type': 'auto'}},
                                         'noValue': no_value},
                            'overrides': overrides or []}}


def logs(title, expr, desc=''):
    return {'type': 'logs', 'title': title, 'description': desc, 'datasource': L,
            'targets': [{'refId': 'A', 'datasource': L, 'expr': expr, 'maxLines': 500}],
            'options': {'showTime': True, 'wrapLogMessage': True, 'sortOrder': 'Descending',
                        'enableLogDetails': True, 'dedupStrategy': 'exact',
                        'prettifyLogMessage': False, 'showLabels': False,
                        'showCommonLabels': False}}


def dash(uid, title, desc, tags, board, templating, refresh='1m', frm='now-6h'):
    return {'uid': uid, 'title': title, 'description': desc, 'tags': tags,
            'timezone': 'browser', 'editable': True, 'schemaVersion': 39, 'version': 1,
            'refresh': refresh, 'graphTooltip': 1,
            'time': {'from': frm, 'to': 'now'}, 'links': nav_links(),
            'templating': {'list': templating}, 'panels': board.panels}


# ---------------------------------------------------------------- shared exprs
CRASHLOOP = 'sum(kube_pod_container_status_waiting_reason{reason="CrashLoopBackOff"}) or vector(0)'
UNAVAIL = 'sum(kube_deployment_status_replicas_unavailable) or vector(0)'
NODE_DOWN = ('count(kube_node_status_condition{condition="Ready", status="true"} == 0) '
             'or vector(0)')
TARGET_DOWN = 'count(up == 0) or vector(0)'
PROBLEMS = '(%s) + (%s) + (%s)' % (CRASHLOOP, UNAVAIL, NODE_DOWN)

SERVER = '192.168.40.208:9100'
CPU_LINUX = ('100 * (1 - avg(rate(node_cpu_seconds_total{instance="%s", mode="idle"}[5m])))')
MEM_LINUX = ('100 * (1 - node_memory_MemAvailable_bytes{instance="%s"} '
             '/ node_memory_MemTotal_bytes{instance="%s"})')
DISK_LINUX = ('100 * (1 - node_filesystem_avail_bytes{instance="%s", mountpoint="/", '
              'fstype!~"tmpfs|overlay|squashfs"} / node_filesystem_size_bytes{instance="%s", '
              'mountpoint="/", fstype!~"tmpfs|overlay|squashfs"})')


# ================================================================ START HERE
def start_here():
    b = Board()
    b.text('', (
        '## Is anything broken?\n'
        'Read the four boxes below. **All green means everything is fine and you can close '
        'this page.** Anything red is listed underneath in plain words, with where to look '
        'next. The graphs at the bottom show whether the server itself is struggling.'),
        h=3)

    b.add(stat('Overall', [t(PROBLEMS, instant=True)],
               steps((None, GREEN), (1, RED)),
               desc='How many things need attention right now, added up from the three '
                    'boxes to the right.',
               mappings=words({0: ('ALL GOOD', GREEN)}), text_mode='value'),
          5, 6, 0, False)
    b.add(stat('Apps not fully running', [t(UNAVAIL, instant=True)],
               steps((None, GREEN), (1, RED)),
               desc='Copies of an app that should be running but are not. 0 is good.',
               mappings=words({0: ('NONE', GREEN)})), 5, 6, 6, False)
    b.add(stat('Apps crashing over and over', [t(CRASHLOOP, instant=True)],
               steps((None, GREEN), (1, RED)),
               desc='Containers stuck in a crash-restart loop (CrashLoopBackOff). These start, '
                    'fail, and get restarted endlessly — nearly always a config problem or a '
                    'dependency (like a database) being unreachable.',
               mappings=words({0: ('NONE', GREEN)})), 5, 6, 12, False)
    b.add(stat('Machines offline', [t(NODE_DOWN, instant=True)],
               steps((None, GREEN), (1, RED)),
               desc='Kubernetes machines that have stopped reporting in. Anything scheduled on '
                    'an offline machine stops working too.',
               mappings=words({0: ('NONE', GREEN)})), 5, 6, 18, True)

    b.row('What is broken, in plain words')
    b.add(table(
        'Apps crashing over and over',
        [t('kube_pod_container_status_waiting_reason{reason="CrashLoopBackOff"} == 1',
           'A', instant=True, fmt='table'),
         t('kube_pod_container_status_restarts_total and on (namespace, pod, container) '
           '(kube_pod_container_status_waiting_reason{reason="CrashLoopBackOff"} == 1)',
           'B', instant=True, fmt='table')],
        {'namespace': 'Area', 'pod': 'App (pod)', 'container': 'Part', 'Value #B': 'Times restarted'},
        hide=('reason', 'Value #A'),
        desc='Open Services, pick this app, and read its logs at the bottom — the reason '
             'for the crash is almost always the last few lines before each restart.',
        sort='Times restarted'), 8, 12, 0, False)
    b.add(table(
        'Apps that are not fully running',
        [t('kube_deployment_status_replicas_unavailable > 0', 'A', instant=True, fmt='table'),
         t('kube_deployment_spec_replicas and on (namespace, deployment) '
           '(kube_deployment_status_replicas_unavailable > 0)', 'B', instant=True,
           fmt='table')],
        {'namespace': 'Area', 'deployment': 'App', 'Value #B': 'Should have',
         'Value #A': 'Missing'}), 8, 12, 12, True)

    b.add(table(
        'Things the monitoring cannot reach',
        [t('label_replace(up == 0, "site", "home", "site", "")', 'A', instant=True,
           fmt='table')],
        {'site': 'Site', 'job': 'What', 'instance': 'Address'},
        hide=('Value', 'role'),
        desc='The monitoring system tried to collect numbers from these and got no answer. '
             'Usually means the thing itself is down.',
        overrides=[]), 6, 12, 0, False)
    b.add(table(
        'Machines offline',
        [t('kube_node_status_condition{condition="Ready", status="true"} == 0', 'A',
           instant=True, fmt='table')],
        {'node': 'Machine'}, hide=('condition', 'status', 'Value'),
        desc='A machine here has stopped reporting in. Everything scheduled on it is '
             'stuck until it comes back.'), 6, 12, 12, True)

    b.row('Is the server struggling?  (MinecraftMachine — runs almost everything)')
    b.add(gauge('CPU busy', [t(CPU_LINUX % SERVER, instant=True)],
                desc='How hard the processor is working. Brief spikes are normal; sitting '
                     'above 90% makes everything slow.'), 5, 6, 0, False)
    b.add(gauge('Memory used', [t(MEM_LINUX % (SERVER, SERVER), instant=True)],
                desc='Above ~90% the system starts killing apps to free memory.',
                warn=80, crit=92), 5, 6, 6, False)
    b.add(gauge('Main disk full', [t(DISK_LINUX % (SERVER, SERVER), instant=True)],
                desc='When this reaches 100% most things stop working. Clean up above 85%.',
                warn=80, crit=90), 5, 6, 12, False)
    b.add(stat('Up for', [t('time() - node_boot_time_seconds{instance="%s"}' % SERVER,
                            instant=True)],
               steps((None, BLUE)), unit='s', decimals=0, color_mode='value',
               desc='Time since the server last rebooted.'), 5, 6, 18, True)

    b.add(ts('CPU and memory over time',
             [t(CPU_LINUX % SERVER, 'A', 'CPU %'),
              t(MEM_LINUX % (SERVER, SERVER), 'B', 'Memory %')],
             unit='percent', minv=0, maxv=100,
             thresholds=steps((None, 'transparent'), (90, 'rgba(242,73,92,0.15)')),
             desc='The shaded band is the danger zone.'), 8, 12, 0, False)
    b.add(ts('App restarts (per hour, by area)',
             [t('sum by (namespace) (increase(kube_pod_container_status_restarts_total[1h])) > 0',
                'A', '{{namespace}}')],
             bars=True, stack=True,
             desc='Any bar here means an app restarted. A tall or growing stack is the '
                  'earliest sign something is going wrong.'), 8, 12, 12, True)
    b.add(ts('Errors in the logs (per 5 min, by area)',
             [{'refId': 'A', 'datasource': L,
               'expr': 'sum by (ns) (label_replace(count_over_time({level="error"}[5m]), '
                       '"ns", "$1", "instance", "([^/]+)/.*"))',
               'legendFormat': '{{ns}}'}],
             ds=L, bars=True, stack=True,
             desc='Error lines written by apps. A sudden jump usually lines up with the '
                  'problem. Open Logs to read them.'), 8, 24, 0, True)

    return dash('home', 'Start Here — Is Anything Broken?',
                'The one page to open first. Four boxes say whether anything is wrong; the '
                'tables under them name what, in plain words; the graphs show whether the '
                'server itself is under strain.',
                ['homelab', 'start-here'], b, [DS_VARS['prom'], DS_VARS['loki']],
                refresh='30s', frm='now-12h')


# ================================================================== SERVICES
WORKLOADS = ('query_result('
             'label_replace(kube_deployment_spec_replicas{namespace="$namespace"} > 0, '
             '"workload", "$1", "deployment", "(.*)") '
             'or label_replace(kube_statefulset_replicas{namespace="$namespace"} > 0, '
             '"workload", "$1", "statefulset", "(.*)") '
             'or label_replace(kube_daemonset_status_desired_number_scheduled'
             '{namespace="$namespace"} > 0, "workload", "$1", "daemonset", "(.*)"))')
# Deployment pods are <name>-<replicaset hash>-<pod hash>; StatefulSet and DaemonSet
# pods have one suffix. Allowing at most two suffix segments stops "cartwise" from
# also matching cartwise-db's pods.
POD = 'pod=~"${workload}-([a-z0-9]+-)?[a-z0-9]+"'
SEL = 'namespace="$namespace", %s' % POD


def services():
    b = Board()
    b.text('', (
        '## How is one service doing?\n'
        'Pick an **Area** and a **Service** at the top. Everything below is about just that '
        'service. Red boxes mean trouble; the logs at the bottom usually say why.'), h=3)

    b.add(stat('Is it running?',
               [t('sum(kube_pod_status_phase{%s, phase="Running"}) or vector(0)' % SEL,
                  instant=True)],
               steps((None, RED), (1, GREEN)),
               desc='How many copies are running right now.',
               mappings=words({0: ('NOT RUNNING', RED)})), 5, 6, 0, False)
    b.add(stat('Ready to serve?',
               [t('min(kube_pod_container_status_ready{%s}) or vector(0)' % SEL,
                  instant=True)],
               steps((None, RED), (1, GREEN)),
               desc='Running is not the same as working. Ready means it passed its own '
                    'health check and is accepting work.',
               mappings=words({0: ('NOT READY', RED), 1: ('READY', GREEN)})), 5, 6, 6, False)
    b.add(stat('Restarts today',
               [t('sum(increase(kube_pod_container_status_restarts_total{%s}[24h])) '
                  'or vector(0)' % SEL, instant=True)],
               steps((None, GREEN), (1, ORANGE), (5, RED)),
               desc='0 is normal. A few can be a one-off. Dozens means it is crash-looping.',
               mappings=words({0: ('NONE', GREEN)})), 5, 6, 12, False)
    b.add(stat('Why it last stopped',
               [t('max by (reason) (kube_pod_container_status_last_terminated_reason{%s})'
                  % SEL, instant=True, legend='{{reason}}')],
               steps((None, GREY)), color_mode='value', text_mode='name',
               desc='OOMKilled = ran out of memory. Error = the app exited with a fault. '
                    'Completed = a normal stop. "no data" = it has never stopped.'),
          5, 6, 18, True)

    b.row('Is it short of resources?')
    b.add(ts('Memory — used vs its limit',
             [t('sum(container_memory_working_set_bytes{%s, container!=""})' % SEL, 'A',
                'used'),
              t('sum(kube_pod_container_resource_limits{%s, resource="memory"})' % SEL, 'B',
                'limit')],
             unit='bytes', minv=0,
             desc='If "used" reaches "limit", Kubernetes kills the app (OOMKilled). No limit '
                  'line means none is set.'), 8, 12, 0, False)
    b.add(ts('CPU — used vs its limit (cores)',
             [t('sum(rate(container_cpu_usage_seconds_total{%s, container!=""}[5m]))' % SEL,
                'A', 'used'),
              t('sum(kube_pod_container_resource_limits{%s, resource="cpu"})' % SEL, 'B',
                'limit')],
             minv=0,
             desc='Hitting the limit does not crash the app, it just makes it slow.'),
          8, 12, 12, True)
    b.add(ts('Network traffic',
             [t('sum(rate(container_network_receive_bytes_total{%s}[5m]))' % SEL, 'A', 'in'),
              t('sum(rate(container_network_transmit_bytes_total{%s}[5m]))' % SEL, 'B', 'out')],
             unit='Bps', minv=0,
             desc='Flat at zero for a service that should be busy is suspicious.'),
          7, 12, 0, False)
    b.add(ts('Restarts over time',
             [t('sum(increase(kube_pod_container_status_restarts_total{%s}[1h]))' % SEL, 'A',
                'restarts / hour')],
             bars=True, minv=0,
             desc='When the restarts started is often the best clue to what changed.'),
          7, 12, 12, True)

    b.row('What is it saying?  (logs)')
    stream = '{instance=~"$namespace/${workload}-.*"}'
    b.add(ts('Log lines by severity',
             [{'refId': 'A', 'datasource': L,
               'expr': 'sum by (level) (count_over_time(%s [$__auto]))' % stream,
               'legendFormat': '{{level}}'}],
             ds=L, bars=True, stack=True,
             desc='Red is errors. A burst of red right before a restart points at the '
                  'cause.'), 6, 24, 0, True)
    b.add(logs('Latest log lines', stream,
               desc='Newest first. Look for the lines just before the app stopped.'),
          14, 24, 0, True)

    templating = [
        DS_VARS['prom'], DS_VARS['loki'],
        {'name': 'namespace', 'label': 'Area', 'type': 'query', 'datasource': P,
         'definition': 'label_values(kube_deployment_spec_replicas, namespace)',
         'query': {'query': 'label_values(kube_deployment_spec_replicas, namespace)',
                   'refId': 'ns'},
         'refresh': 1, 'sort': 1, 'includeAll': False, 'multi': False, 'hide': 0,
         'current': {'text': 'pantry-bot', 'value': 'pantry-bot'}, 'regex': ''},
        {'name': 'workload', 'label': 'Service', 'type': 'query', 'datasource': P,
         'definition': WORKLOADS, 'query': {'query': WORKLOADS, 'refId': 'wl'},
         'regex': '/workload="([^"]+)"/', 'refresh': 2, 'sort': 1,
         'includeAll': False, 'multi': False, 'hide': 0, 'current': {}},
    ]
    return dash('services', 'Services — How Is One Service Doing?',
                'Pick an area and a service; every panel is about that one service. The '
                'service list is read live from Kubernetes, so new services appear on their '
                'own. Only services that are supposed to be running (1+ copies) are listed.',
                ['homelab', 'services'], b, templating)


# ================================================================== MACHINES
def machines():
    b = Board()
    b.text('', (
        '## How is one machine doing?\n'
        'Pick a **Machine** at the top. Big dials: green is fine, orange is getting tight, '
        'red needs attention. **chasebot is not listed** because nothing collects its '
        'hardware numbers yet (see issue #372).'), h=3)

    M = '$machine'
    cpu = [t(CPU_LINUX % M, 'A', 'CPU %', instant=True),
           t('100 * (1 - avg(rate(windows_cpu_time_total{instance="%s", mode="idle"}[5m])))'
             % M, 'B', 'CPU %', instant=True)]
    mem = [t(MEM_LINUX % (M, M), 'A', instant=True),
           t('100 * (1 - windows_memory_available_bytes{instance="%s"} '
             '/ windows_memory_physical_total_bytes{instance="%s"})' % (M, M), 'B',
             instant=True)]
    disk = [t('max(%s)' % (DISK_LINUX % (M, M)), 'A', instant=True),
            t('max(100 * (1 - windows_logical_disk_free_bytes{instance="%s"} '
              '/ windows_logical_disk_size_bytes{instance="%s"}))' % (M, M), 'B',
              instant=True)]
    up = [t('time() - node_boot_time_seconds{instance="%s"}' % M, 'A', instant=True),
          t('time() - windows_system_boot_time_timestamp{instance="%s"}' % M, 'B',
            instant=True)]

    b.add(gauge('CPU busy', cpu, desc='Sustained above 90% makes everything slow.'),
          6, 6, 0, False)
    b.add(gauge('Memory used', mem, warn=80, crit=92,
                desc='Above ~90% the system starts killing programs to free memory.'),
          6, 6, 6, False)
    b.add(gauge('Fullest disk', disk, warn=80, crit=90,
                desc='The fullest drive on this machine. At 100% things stop working.'),
          6, 6, 12, False)
    b.add(stat('Up for', up, steps((None, BLUE)), unit='s', color_mode='value',
               desc='Time since the last reboot. A surprisingly small number means it '
                    'restarted on its own.'), 6, 6, 18, True)

    b.row('Over time')
    b.add(ts('CPU and memory',
             [t(CPU_LINUX % M, 'A', 'CPU %'),
              t(MEM_LINUX % (M, M), 'B', 'Memory %'),
              t('100 * (1 - avg(rate(windows_cpu_time_total{instance="%s", mode="idle"}[5m])))'
                % M, 'C', 'CPU %'),
              t('100 * (1 - windows_memory_available_bytes{instance="%s"} '
                '/ windows_memory_physical_total_bytes{instance="%s"})' % (M, M), 'D',
                'Memory %')],
             unit='percent', minv=0, maxv=100,
             thresholds=steps((None, 'transparent'), (90, 'rgba(242,73,92,0.15)')),
             desc='The shaded band is the danger zone.'), 8, 12, 0, False)
    b.add(ts('Disk space used, per drive',
             [t('100 * (1 - node_filesystem_avail_bytes{instance="%s", '
                'fstype!~"tmpfs|overlay|squashfs|iso9660"} / node_filesystem_size_bytes'
                '{instance="%s", fstype!~"tmpfs|overlay|squashfs|iso9660"})' % (M, M), 'A',
                '{{mountpoint}}'),
              t('100 * (1 - windows_logical_disk_free_bytes{instance="%s"} '
                '/ windows_logical_disk_size_bytes{instance="%s"})' % (M, M), 'B',
                '{{volume}}')],
             unit='percent', minv=0, maxv=100,
             thresholds=steps((None, 'transparent'), (85, 'rgba(255,152,48,0.15)')),
             desc='A line climbing steadily towards the top will eventually fill up.'),
          8, 12, 12, True)
    b.add(ts('Network traffic',
             [t('sum(rate(node_network_receive_bytes_total{instance="%s", '
                'device!~"lo|veth.*|docker.*|br-.*|cni.*|flannel.*|cali.*|virbr.*"}[5m]))' % M,
                'A', 'download'),
              t('sum(rate(node_network_transmit_bytes_total{instance="%s", '
                'device!~"lo|veth.*|docker.*|br-.*|cni.*|flannel.*|cali.*|virbr.*"}[5m]))' % M,
                'B', 'upload'),
              t('sum(rate(windows_net_bytes_received_total{instance="%s"}[5m]))' % M, 'C',
                'download'),
              t('sum(rate(windows_net_bytes_sent_total{instance="%s"}[5m]))' % M, 'D',
                'upload')],
             unit='Bps', minv=0), 8, 12, 0, False)
    b.add(ts('Temperature (hottest sensor)',
             [t('max(node_hwmon_temp_celsius{instance="%s"})' % M, 'A', 'hottest')],
             unit='celsius', minv=0,
             thresholds=steps((None, 'transparent'), (80, 'rgba(242,73,92,0.15)')),
             desc='Server only — the Windows PC does not report temperatures. Sustained '
                  'above 80°C means a fan or airflow problem.'), 8, 12, 12, True)

    b.row("Graphics card  (Greenie's PC only)")
    b.add(ts('GPU busy, by engine',
             [t('sum by (engtype) (rate(windows_gpu_engine_time_seconds{instance="%s"}[5m])) '
                '* 100' % M, 'A', '{{engtype}}')],
             unit='percent', minv=0,
             desc='Empty on the server, which has no monitored GPU.'), 8, 12, 0, False)
    b.add(ts('GPU memory',
             [t('sum(windows_gpu_adapter_memory_dedicated_bytes{instance="%s"})' % M, 'A',
                'dedicated'),
              t('sum(windows_gpu_adapter_memory_shared_bytes{instance="%s"})' % M, 'B',
                'shared')],
             unit='bytes', minv=0), 8, 12, 12, True)

    templating = [
        DS_VARS['prom'],
        {'name': 'machine', 'label': 'Machine', 'type': 'custom',
         'query': 'MinecraftMachine (the server) : %s, Greenie\'s PC : ThePantry' % SERVER,
         'options': [
             {'text': 'MinecraftMachine (the server)', 'value': SERVER, 'selected': True},
             {'text': "Greenie's PC", 'value': 'ThePantry', 'selected': False}],
         'current': {'text': 'MinecraftMachine (the server)', 'value': SERVER},
         'includeAll': False, 'multi': False, 'hide': 0},
    ]
    return dash('machines', 'Machines — How Is One Machine Doing?',
                'Hardware health for one machine at a time. Each panel carries a Linux query '
                '(node_exporter) and a Windows query (windows_exporter); only the one '
                'matching the chosen machine returns data, so one dashboard covers both kinds.',
                ['homelab', 'machines'], b, templating)


# ================================================================= PANTRY BOT
PB = 'namespace="pantry-bot"'


def pantry_bot():
    b = Board()
    b.text('', (
        '## PantryBot\n'
        'Top half: **is it working?** Bottom half: **is anyone using it?** If the top is red, '
        'the bottom will be empty — fix the top first. For one part in detail, open '
        'Services and pick Area `pantry-bot`.'), h=3)

    b.add(stat('Parts running',
               [t('sum(kube_deployment_status_replicas_available{%s})' % PB, 'A',
                  'running', instant=True),
                t('sum(kube_deployment_spec_replicas{%s})' % PB, 'B', 'should be',
                  instant=True)],
               steps((None, BLUE)), color_mode='value', text_mode='value_and_name',
               desc='Parts of PantryBot running vs how many should be.'), 5, 6, 0, False)
    b.add(stat('Parts crashing',
               [t('sum(kube_pod_container_status_waiting_reason{%s, reason="CrashLoopBackOff"}) '
                  'or vector(0)' % PB, instant=True)],
               steps((None, GREEN), (1, RED)), mappings=words({0: ('NONE', GREEN)}),
               desc='Parts stuck in a crash-restart loop.'), 5, 6, 6, False)
    b.add(stat('Database reachable?',
               [t('max(kube_endpoint_address_available{%s, endpoint=~"postgres-authority.*"}) '
                  'or vector(0)' % PB, instant=True)],
               steps((None, RED), (1, GREEN)),
               mappings=words({0: ('NO', RED)}),
               desc='Whether any PantryBot database copy is up and accepting connections. '
                    'Everything else depends on this — if it says NO, fix it first.'),
          5, 6, 12, False)
    b.add(stat('Restarts today',
               [t('sum(increase(kube_pod_container_status_restarts_total{%s}[24h])) '
                  'or vector(0)' % PB, instant=True)],
               steps((None, GREEN), (1, ORANGE), (10, RED)),
               mappings=words({0: ('NONE', GREEN)})), 5, 6, 18, True)

    b.add(table('Every PantryBot part',
                [t('sum by (deployment) (kube_deployment_spec_replicas{%s}) > 0' % PB, 'A',
                   instant=True, fmt='table'),
                 t('sum by (deployment) (kube_deployment_status_replicas_available{%s}) '
                   'and on (deployment) (kube_deployment_spec_replicas{%s} > 0)' % (PB, PB),
                   'B', instant=True, fmt='table')],
                {'deployment': 'Part', 'Value #A': 'Should have', 'Value #B': 'Running'},
                desc='Parts deliberately switched off (0 copies) are not listed.',
                overrides=[{'matcher': {'id': 'byName', 'options': 'Running'},
                            'properties': [
                                {'id': 'custom.cellOptions',
                                 'value': {'type': 'color-background', 'mode': 'basic'}},
                                {'id': 'thresholds', 'value': steps((None, RED), (1, GREEN))}]}]),
          8, 24, 0, True)

    b.row('Is anyone using it?')
    b.add(ts('Commands used (per 15 min)',
             [t('sum by (command) (increase(pantry_bot_commands_total[15m]))', 'A',
                '{{command}}')],
             bars=True, stack=True, minv=0,
             desc='Chat commands people ran. Empty while the bot is down.'), 8, 12, 0, False)
    b.add(ts('Work picked up vs finished (per 15 min)',
             [t('sum(increase(pantry_bot_runtime_claims_total[15m]))', 'A', 'picked up'),
              t('sum(increase(pantry_bot_runtime_completions_total[15m]))', 'B', 'finished')],
             minv=0,
             desc='The two lines should track each other. "Picked up" pulling away from '
                  '"finished" means work is getting stuck.'), 8, 12, 12, True)

    b.row('From the database  (keeps working even when the bot is down, as long as the '
          'database is up)')
    b.add({'type': 'stat', 'title': 'Events received (last hour)', 'datasource': PG,
           'targets': [{'refId': 'A', 'datasource': PG, 'format': 'table',
                        'rawSql': "SELECT count(*) AS events FROM pantry_events "
                                  "WHERE created_at > now() - interval '1 hour'"}],
           'options': {'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '',
                                         'values': False},
                       'colorMode': 'value', 'graphMode': 'none', 'textMode': 'auto'},
           'fieldConfig': {'defaults': {'noValue': 'database unreachable',
                                        'thresholds': steps((None, BLUE))},
                           'overrides': []}}, 5, 8, 0, False)
    b.add({'type': 'stat', 'title': 'Messages stuck undelivered', 'datasource': PG,
           'description': 'Outgoing messages that failed and were set aside. Should be 0.',
           'targets': [{'refId': 'A', 'datasource': PG, 'format': 'table',
                        'rawSql': "SELECT count(*) AS dead_letter FROM pantry_outbox "
                                  "WHERE status = 'dead_letter'"}],
           'options': {'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '',
                                         'values': False},
                       'colorMode': 'background', 'graphMode': 'none', 'textMode': 'auto'},
           'fieldConfig': {'defaults': {'noValue': 'database unreachable',
                                        'thresholds': steps((None, GREEN), (1, RED))},
                           'overrides': []}}, 5, 8, 8, False)
    b.add({'type': 'stat', 'title': 'Different people who have taken part', 'datasource': PG,
           'description': 'Everyone who has ever joined in, all time.',
           'targets': [{'refId': 'A', 'datasource': PG, 'format': 'table',
                        'rawSql': "SELECT count(DISTINCT user_id) AS participants "
                                  "FROM community_participants"}],
           'options': {'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '',
                                         'values': False},
                       'colorMode': 'value', 'graphMode': 'none', 'textMode': 'auto'},
           'fieldConfig': {'defaults': {'noValue': 'database unreachable',
                                        'thresholds': steps((None, BLUE))},
                           'overrides': []}}, 5, 8, 16, True)
    b.add({'type': 'timeseries', 'title': 'Events received over time', 'datasource': PG,
           'targets': [{'refId': 'A', 'datasource': PG, 'format': 'time_series',
                        'rawSql': 'SELECT $__timeGroup(created_at, $__interval) AS "time", '
                                  'source AS metric, count(*) AS events FROM pantry_events '
                                  'WHERE $__timeFilter(created_at) GROUP BY 1, 2 ORDER BY 1'}],
           'options': {'legend': {'displayMode': 'list', 'placement': 'bottom'},
                       'tooltip': {'mode': 'multi'}},
           'fieldConfig': {'defaults': {'custom': {'drawStyle': 'bars', 'fillOpacity': 60,
                                                   'stacking': {'mode': 'normal',
                                                                'group': 'A'}},
                                        'noValue': 'database unreachable'},
                           'overrides': []}}, 8, 24, 0, True)

    b.row('What is it saying?  (logs)')
    b.add(logs('PantryBot errors and warnings',
               '{instance=~"pantry-bot/.*", level=~"error|warn"}',
               desc='Only errors and warnings, newest first.'), 12, 24, 0, True)

    return dash('homelab-pantry-bot', 'PantryBot — Health & Usage',
                'Is PantryBot working, and is anyone using it. Replaces the three separate '
                'PantryBot dashboards (health, usage, usage-from-Postgres).',
                ['homelab', 'pantry-bot'], b,
                [DS_VARS['prom'], DS_VARS['loki'], DS_VARS['pg']])


# ====================================================================== main
def main():
    for d, name in [(start_here(), 'start-here.json'), (services(), 'services.json'),
                    (machines(), 'machines.json'), (pantry_bot(), 'pantry-bot.json')]:
        path = os.path.join(OUT, name)
        with open(path, 'w') as fh:
            json.dump(d, fh, indent=1, ensure_ascii=False)
            fh.write('\n')
        print('%-22s %2d panels  uid=%s' % (name, len(d['panels']), d['uid']))


if __name__ == '__main__':
    main()
