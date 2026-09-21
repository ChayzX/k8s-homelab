#!/usr/bin/env python3
"""Regenerate the Grafana dashboard ConfigMaps from the JSON sources.

Two ConfigMaps, because one would approach the 1 MiB ConfigMap ceiling and
because the per-service dashboards belong in their own Grafana folder:

  dashboards/*.json          -> grafana-dashboards          (folder: General)
  dashboards/services/*.json -> grafana-dashboards-services (folder: Services)

scripts/validate-grafana-dashboards.sh compares each embedded copy against
its source byte for byte, so the embedding here is verbatim: each line of
the source file indented by four spaces under a literal block scalar.
"""
import glob
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TARGETS = [
    dict(src=os.path.join(REPO, 'dashboards', '*.json'),
         out=os.path.join(REPO, 'dashboards', 'dashboards-configmap.yaml'),
         name='grafana-dashboards'),
    dict(src=os.path.join(REPO, 'dashboards', 'services', '*.json'),
         out=os.path.join(REPO, 'dashboards', 'services-configmap.yaml'),
         name='grafana-dashboards-services'),
]

LIMIT = 900000


def embed(path):
    with open(path) as fh:
        body = fh.read()
    if body.endswith('\n'):
        body = body[:-1]
    return '\n'.join(('    ' + line) if line else '' for line in body.split('\n'))


def render(name, files):
    out = ['apiVersion: v1', 'data:']
    for f in files:
        out.append('  %s: |' % os.path.basename(f))
        out.append(embed(f))
    out += ['kind: ConfigMap', 'metadata:',
            '  name: %s' % name, '  namespace: observability', '']
    return '\n'.join(out)


def main():
    for t in TARGETS:
        files = sorted(glob.glob(t['src']))
        if not files:
            raise SystemExit('no dashboard JSON found for %s' % t['src'])
        text = render(t['name'], files)
        if len(text) >= LIMIT:
            raise SystemExit('%s would be %d bytes; split it before the 1 MiB limit'
                             % (t['out'], len(text)))
        with open(t['out'], 'w') as fh:
            fh.write(text)
        print('%s: %d dashboards, %d bytes'
              % (os.path.relpath(t['out'], REPO), len(files), len(text)))


if __name__ == '__main__':
    main()
