#!/usr/bin/env python3
"""Probe the latest external-monitor notification receipt."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from monitor import notification_receipt_probe


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state-file",
        default="/var/lib/homelab-monitor/state.json",
        type=Path,
    )
    parser.add_argument("--identity", required=True)
    parser.add_argument("--event", choices=("firing", "recovered"), required=True)
    parser.add_argument("--max-age-seconds", type=int, default=900)
    args = parser.parse_args()

    try:
        state = json.loads(args.state_file.read_text())
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "reason": type(exc).__name__}))
        return 1

    ok, result = notification_receipt_probe(
        state,
        identity=args.identity,
        event=args.event,
        max_age_seconds=args.max_age_seconds,
    )
    if ok:
        print(json.dumps({"ok": True, "receipt": result}, sort_keys=True))
        return 0
    print(json.dumps({"ok": False, "reason": result}, sort_keys=True))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
