import json, os, tempfile, unittest
from cloudflare_route_adapter import apply_routes, desired_config, load_inputs, resolve_token, site_tunnel_configs, RouteError

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

class CloudflareRouteAdapterTest(unittest.TestCase):
    def test_desired_config_has_catchall(self):
        _, cfg=desired_config(data()["sites"]["canada"], "application")
        self.assertEqual(cfg["ingress"][-1], {"service":"http_status:404"})

    def test_dry_run_does_not_put_and_clears_standby(self):
        f=Fake(); changes=apply_routes(data(), "canada", f, apply=False)
        self.assertEqual(len(changes), 4); self.assertEqual(f.puts, [])

    def test_apply_sets_active_and_excludes_standby(self):
        f=Fake(); apply_routes(data(), "canada", f, apply=True)
        self.assertEqual(len(f.puts), 4)
        active=[c for tid,c in f.puts if tid == "a"][0]
        standby=[c for tid,c in f.puts if tid == "oa"][0]
        self.assertEqual(active["ingress"][0]["hostname"], "mods.example")
        self.assertEqual(standby["ingress"], [{"service":"http_status:404"}])
        self.assertEqual(standby["warp-routing"], {"enabled": False})

    def test_inputs_round_trip(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data(), f); path=f.name
        self.assertEqual(load_inputs(path)["account_id"], "acct")

    def test_combined_canada_connector_has_both_route_sets_once(self):
        site = {"connector_tunnel_id":"canada", "application_hostnames":["mods.example","overlay.example"],
                "commands_hostnames":["commands.example"], "application_origin":"http://app",
                "commands_origin":"http://cmd"}
        cfgs = site_tunnel_configs(site)
        self.assertEqual(list(cfgs), ["canada"])
        self.assertEqual(cfgs["canada"]["ingress"], [
            {"hostname":"mods.example","service":"http://app"},
            {"hostname":"overlay.example","service":"http://app"},
            {"hostname":"commands.example","service":"http://cmd"},
            {"service":"http_status:404"},
        ])

    def test_token_env_wins_over_file(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("file-token\n"); f.flush()
            self.assertEqual(resolve_token({"CLOUDFLARE_API_TOKEN": "env-token", "CLOUDFLARE_API_TOKEN_FILE": f.name}), "env-token")

    def test_token_reads_file_and_strips(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("file-token\n"); f.flush()
            self.assertEqual(resolve_token({"CLOUDFLARE_API_TOKEN": "", "CLOUDFLARE_API_TOKEN_FILE": f.name}), "file-token")

    def test_token_missing_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RouteError):
                resolve_token({"CLOUDFLARE_API_TOKEN": "", "CLOUDFLARE_API_TOKEN_FILE": os.path.join(d, "missing")})

if __name__ == "__main__":
    unittest.main()