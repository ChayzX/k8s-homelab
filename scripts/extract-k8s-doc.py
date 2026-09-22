#!/usr/bin/env python3
"""Print a single document out of a multi-document Kubernetes manifest.

Used by .github/workflows/grafana-deploy.yml. The ci-deploy ServiceAccount
is deliberately scoped to ConfigMaps and Deployments, so it cannot apply the
ServiceAccount/PVC/Service that observability/grafana.yaml also defines --
`kubectl apply -f` on the whole file fails on those even when they are
unchanged. Extracting just the document CI is allowed to manage keeps the
CI credential least-privilege instead of widening its RBAC.

    extract-k8s-doc.py <file> <kind> [name]
"""
import sys

import yaml


def main(argv):
    if not 3 <= len(argv) <= 4:
        sys.exit(__doc__)
    path, kind = argv[1], argv[2]
    name = argv[3] if len(argv) == 4 else None

    docs = [d for d in yaml.safe_load_all(open(path))
            if d and d.get('kind') == kind
            and (name is None or d.get('metadata', {}).get('name') == name)]
    if len(docs) != 1:
        sys.exit('%s: expected exactly one %s%s, found %d'
                 % (path, kind, '/%s' % name if name else '', len(docs)))
    yaml.safe_dump(docs[0], sys.stdout, default_flow_style=False)


if __name__ == '__main__':
    main(sys.argv)
