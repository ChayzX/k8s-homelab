#!/usr/bin/env python3
"""Point the per-site application hostnames' DNS at one site's tunnel.

cloudflare_route_adapter.py only rewrites tunnel *ingress*; a hostname's
proxied CNAME still names exactly one tunnel, so switching ingress alone
leaves traffic on the old tunnel (found live 2026-09-21: overlay went
502 -> 404 after an adapter apply because its CNAME still named Oracle's
tunnel). This moves the CNAMEs. Dry-run unless --apply. Never prints the
API token.
"""
import argparse
import json
import os
import sys
from urllib.request import Request, urlopen

API = "https://api.cloudflare.com/client/v4"


def token() -> str:
    value = os.environ.get("CLOUDFLARE_API_TOKEN")
    if not value:
        with open("/etc/failover-witness/cloudflare-token") as handle:
            value = handle.read().strip()
    if "=" in value and value.split("=", 1)[0].isupper():
        value = value.split("=", 1)[1].strip()
    if not value:
        raise SystemExit("cloudflare token is empty")
    return value


def call(method: str, path: str, secret: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = Request(f"{API}{path}", data=data, method=method,
                  headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"})
    with urlopen(req, timeout=15) as response:
        result = json.loads(response.read())
    if not result.get("success"):
        raise SystemExit(f"cloudflare error on {method} {path}: {result.get('errors')}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs", default="/etc/failover-witness/cloudflare-route-inputs.json")
    parser.add_argument("--active", required=True, choices=["home", "oracle", "canada"])
    parser.add_argument("--zone", default="greeniespantry.uk")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    site = json.load(open(args.inputs))["sites"][args.active]
    tunnel = site.get("connector_tunnel_id") or site.get("application_tunnel_id")
    hostnames = [route["hostname"] for route in site["application_routes"]]
    target = f"{tunnel}.cfargotunnel.com"
    secret = token()

    zone_id = call("GET", f"/zones?name={args.zone}", secret)["result"][0]["id"]
    ok = True
    for name in hostnames:
        records = call("GET", f"/zones/{zone_id}/dns_records?name={name}", secret)["result"]
        if len(records) != 1 or records[0]["type"] != "CNAME":
            print(f"{name}: unexpected records {[(r['type'], r['content']) for r in records]} - refusing")
            ok = False
            continue
        record = records[0]
        if record["content"] == target:
            print(f"{name}: already -> {target}")
            continue
        print(f"{name}: {record['content']} -> {target}{'' if args.apply else ' (dry-run)'}")
        if args.apply:
            call("PATCH", f"/zones/{zone_id}/dns_records/{record['id']}", secret,
                 {"content": target, "proxied": record.get("proxied", True)})
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
