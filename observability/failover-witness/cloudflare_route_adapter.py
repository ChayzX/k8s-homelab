#!/usr/bin/env python3
"""Authority-aware Cloudflare tunnel route adapter.

Dry-run is the default.  Set ``--apply`` only from the failover controller after
the writer fence, promotion, and health gates have succeeded.  Tokens are read
from CLOUDFLARE_API_TOKEN and are never included in output or exceptions.
"""
from __future__ import annotations

import argparse, json, os, sys
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class RouteError(RuntimeError):
    pass


@dataclass
class Tunnel:
    tunnel_id: str
    origin: str


class CloudflareClient:
    def __init__(self, account_id: str, token: str, opener=urlopen):
        self.base = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/cfd_tunnel"
        self.token = token
        self.opener = opener

    def configuration(self, tunnel_id: str) -> dict:
        return self._request("GET", f"/{tunnel_id}/configurations")

    def put_configuration(self, tunnel_id: str, config: dict) -> dict:
        return self._request("PUT", f"/{tunnel_id}/configurations", {"config": config})

    def _request(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = Request(self.base + path, data=data, method=method,
                      headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with self.opener(req, timeout=20) as response:
                parsed = json.loads(response.read())
        except (HTTPError, URLError, TimeoutError) as exc:
            raise RouteError(f"Cloudflare request failed: {getattr(exc, 'code', 'network error')}") from None
        if not parsed.get("success"):
            raise RouteError("Cloudflare rejected tunnel configuration")
        return parsed.get("result", {})


def load_inputs(path):
    try:
        with open(path, encoding="utf-8") as f: data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc: raise RouteError(f"invalid route input: {exc}")
    account = data.get("account_id")
    sites = data.get("sites", {})
    if not account or not isinstance(sites, dict): raise RouteError("account_id and sites are required")
    for name, site in sites.items():
        for key in ("application_tunnel_id", "commands_tunnel_id"):
            if site.get(key) is not None and not isinstance(site[key], str): raise RouteError(f"{name}.{key} must be a string")
    return data


def desired_config(site, kind):
    tunnel_id = site.get("application_tunnel_id" if kind == "application" else "commands_tunnel_id")
    if not tunnel_id: return None, None
    route_key = "application_routes" if kind == "application" else "commands_routes"
    routes = site.get(route_key)
    if routes is None:
        hostnames = site.get("application_hostnames" if kind == "application" else "commands_hostnames", [])
        origin = site.get("application_origin" if kind == "application" else "commands_origin")
        routes = [{"hostname": h, "service": origin} for h in hostnames]
    if not isinstance(routes, list) or any(not isinstance(r, dict) or not r.get("hostname") or not r.get("service") for r in routes):
        raise RouteError(f"missing {kind} route data")
    ingress = [{"hostname": r["hostname"], "service": r["service"]} for r in routes]
    ingress.append({"service": "http_status:404"})
    return tunnel_id, {"ingress": ingress, "warp-routing": {"enabled": False}}


def site_tunnel_configs(site):
    """Return the complete desired config for each tunnel used by a site."""
    combined = site.get("connector_tunnel_id")
    if combined:
        app_cfg = desired_config({**site, "application_tunnel_id": combined}, "application")[1]
        cmd_cfg = desired_config({**site, "commands_tunnel_id": combined}, "commands")[1]
        ingress = app_cfg["ingress"][:-1] + cmd_cfg["ingress"][:-1]
        ingress.append({"service": "http_status:404"})
        return {combined: {"ingress": ingress, "warp-routing": {"enabled": False}}}
    result = {}
    for kind in ("application", "commands"):
        tid, cfg = desired_config(site, kind)
        if tid:
            result[tid] = cfg
    return result


def apply_routes(data, active, client, apply=False):
    sites = data["sites"]
    if active not in sites: raise RouteError(f"unknown active site: {active}")
    # A tunnel may be shared by sites with identical origins (Home and Oracle
    # share PantryBot-App: same k8s service names, and only the active site
    # runs its connector - #191). Sharing sites must want the exact same
    # config; the tunnel serves routes when ANY of its sites is active.
    tunnels = {}
    for site_name, site in sites.items():
        for tid, wanted in site_tunnel_configs(site).items():
            entry = tunnels.setdefault(tid, {"sites": [], "wanted": wanted, "combined": bool(site.get("connector_tunnel_id"))})
            if entry["wanted"] != wanted:
                raise RouteError(f"tunnel {tid} is shared by sites with different routes")
            entry["sites"].append(site_name)
    changes = []
    for tid, entry in tunnels.items():
        current = client.configuration(tid).get("config", {})
        target = entry["wanted"] if active in entry["sites"] else {"ingress": [{"service": "http_status:404"}], "warp-routing": {"enabled": False}}
        if current != target:
            changes.append(("+".join(entry["sites"]), "combined" if entry["combined"] else "split", tid, target))
            if apply: client.put_configuration(tid, target)
    return changes
DEFAULT_TOKEN_FILE = "/etc/failover-witness/cloudflare-token"


def resolve_token(env=None):
    token = (os.environ if env is None else env).get("CLOUDFLARE_API_TOKEN")
    if token:
        return token
    path = (os.environ if env is None else env).get("CLOUDFLARE_API_TOKEN_FILE", DEFAULT_TOKEN_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError as exc:
        raise RouteError(f"cloudflare token unavailable: {exc}") from None


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--inputs", required=True); p.add_argument("--active", required=True)
    p.add_argument("--apply", action="store_true", help="mutate tunnel configurations")
    a = p.parse_args(argv)
    try:
        data = load_inputs(a.inputs)
        token = resolve_token()
        if not token: raise RouteError("CLOUDFLARE_API_TOKEN is required")
        changes = apply_routes(data, a.active, CloudflareClient(data["account_id"], token), a.apply)
        print(json.dumps({"mode": "apply" if a.apply else "dry-run", "active": a.active,
                          "changes": [{"site": s, "kind": k, "tunnel_id": t} for s,k,t,_ in changes]}))
        return 0
    except RouteError as exc: print(str(exc), file=sys.stderr); return 2

if __name__ == "__main__": sys.exit(main())
