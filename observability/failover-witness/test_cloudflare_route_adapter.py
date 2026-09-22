import json, tempfile
from cloudflare_route_adapter import apply_routes, desired_config, load_inputs, site_tunnel_configs

class Fake:
    def __init__(self): self.configs={k:{"config":{"ingress":[{"service":"old"}]}} for k in ("a","c","oa","oc")}; self.puts=[]
    def configuration(self, tid): return self.configs[tid]
    def put_configuration(self, tid, config): self.puts.append((tid, config)); return {}

def data():
    return {"account_id":"acct", "sites": {
      "canada":{"application_tunnel_id":"a","commands_tunnel_id":"c",
        "application_hostnames":["mods.example"],"commands_hostnames":["commands.example"],
        "application_origin":"http://app","commands_origin":"http://cmd"},
      "oracle":{"application_tunnel_id":"oa","commands_tunnel_id":"oc",
        "application_hostnames":["mods.example"],"commands_hostnames":["commands.example"],
        "application_origin":"http://oa","commands_origin":"http://oc"}}}

def test_desired_config_has_catchall():
    _, cfg=desired_config(data()["sites"]["canada"], "application")
    assert cfg["ingress"][-1] == {"service":"http_status:404"}

def test_dry_run_does_not_put_and_clears_standby():
    f=Fake(); changes=apply_routes(data(), "canada", f, apply=False)
    assert len(changes) == 4 and f.puts == []

def test_apply_sets_active_and_excludes_standby():
    f=Fake(); apply_routes(data(), "canada", f, apply=True)
    assert len(f.puts) == 4
    active=[c for tid,c in f.puts if tid == "a"][0]
    standby=[c for tid,c in f.puts if tid == "oa"][0]
    assert active["ingress"][0]["hostname"] == "mods.example"
    assert standby["ingress"] == [{"service":"http_status:404"}]
    assert standby["warp-routing"] == {"enabled": False}

def test_inputs_round_trip():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data(), f); path=f.name
    assert load_inputs(path)["account_id"] == "acct"

def test_combined_canada_connector_has_both_route_sets_once():
    site = {"connector_tunnel_id":"canada", "application_hostnames":["mods.example","overlay.example"],
            "commands_hostnames":["commands.example"], "application_origin":"http://app",
            "commands_origin":"http://cmd"}
    cfgs = site_tunnel_configs(site)
    assert list(cfgs) == ["canada"]
    assert cfgs["canada"]["ingress"] == [
        {"hostname":"mods.example","service":"http://app"},
        {"hostname":"overlay.example","service":"http://app"},
        {"hostname":"commands.example","service":"http://cmd"},
        {"service":"http_status:404"},
    ]


def shared_data():
    d = data()
    d["sites"]["home"] = dict(d["sites"]["oracle"])
    return d

def test_shared_tunnel_serves_when_either_sharing_site_is_active():
    for active in ("home", "oracle"):
        f=Fake(); apply_routes(shared_data(), active, f, apply=True)
        puts=dict(f.puts)
        assert puts["oa"]["ingress"][0]["hostname"] == "mods.example"
        assert puts["a"]["ingress"] == [{"service":"http_status:404"}]
        assert sum(1 for tid, _ in f.puts if tid == "oa") == 1

def test_shared_tunnel_is_closed_when_the_other_site_is_active():
    f=Fake(); apply_routes(shared_data(), "canada", f, apply=True)
    assert dict(f.puts)["oa"]["ingress"] == [{"service":"http_status:404"}]

def test_shared_tunnel_with_different_routes_is_refused():
    import pytest
    from cloudflare_route_adapter import RouteError
    d = shared_data(); d["sites"]["home"]["application_origin"] = "http://different"
    with pytest.raises(RouteError):
        apply_routes(d, "home", Fake(), apply=False)
