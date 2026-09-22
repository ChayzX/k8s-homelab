#!/usr/bin/env python3
"""Behavioral tests for the shared PantryBot writer-domain fence (#191)."""

import ast
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ORACLE = ROOT / "fence-oracle-postgres-local.sh"
HOME = ROOT / "fence-home-direct-from-oracle.sh"
WRITERS = [
    "pantry-private-api", "pantry-overlay-delivery", "pantry-twitch-gateway",
    "pantry-twitch-dispatcher", "pantry-chat-worker", "pantry-private-site", "app-cloudflared",
]

# Minimal stateful kubectl: statefulsets, deployments and pods in one JSON file.
FAKE_KUBECTL = r'''#!/usr/bin/env python3
import json, os, sys
path = os.environ["FAKE_STATE"]
state = json.load(open(path))
args = [a for a in sys.argv[1:] if not a.startswith(("--kubeconfig=", "--request-timeout="))]
if args[:1] == ["-n"]:
    args = args[2:]
state["calls"].append(args)
def save():
    json.dump(state, open(path, "w"))
def err(msg):
    save(); print(msg, file=sys.stderr); sys.exit(1)
verb = args[0]
if verb == "version":
    if state.get("probe_error"):
        err(state["probe_error"])
    save(); sys.exit(0)
kind, name = args[1], args[2]
table = {"statefulset": "statefulsets", "deployment": "deployments", "pod": "pods"}[kind]
if name in state["forbidden"]:
    err("Error from server (Forbidden): forbidden")
if name not in state[table]:
    err(f'Error from server (NotFound): {table} "{name}" not found')
if verb == "get":
    jp = args[args.index("-o") + 1]
    obj = state[table][name]
    if ".metadata.name" in jp:
        out = name
    elif kind == "statefulset":
        out = str(obj)
    elif kind == "deployment":
        out = f"{obj[0]}/{obj[1] or ''}"
    else:
        out = obj
    save(); print(out, end=""); sys.exit(0)
if verb == "patch":
    if kind == "statefulset":
        state[table][name] = 0
    else:
        state[table][name] = [0, 0 if state.get("deploy_drains", True) else state[table][name][1]]
    save(); sys.exit(0)
if verb == "delete":
    if state["pods"][name] not in state["notready_nodes"]:
        del state["pods"][name]
    save(); sys.exit(0)
err("unsupported: " + " ".join(args))
'''

OVERRIDES = (
    "PANTRY_STANDBY_STATEFULSET", "PANTRY_HOME_STATEFULSET", "PANTRY_FENCE_STATEFULSETS",
    "PANTRY_FENCE_DEPLOYMENTS", "PANTRY_HOME_STATEFULSETS", "PANTRY_HOME_FENCE_DEPLOYMENTS",
)


def state(**kw):
    base = {"statefulsets": {}, "pods": {}, "deployments": {}, "forbidden": [],
            "notready_nodes": [], "probe_error": None, "calls": []}
    base.update(kw)
    return base


def run_fence(script: Path, initial: dict, extra_env: dict | None = None, args=("--confirm",)):
    with tempfile.TemporaryDirectory() as tmp:
        kubectl = Path(tmp) / "kubectl"
        kubectl.write_text(FAKE_KUBECTL)
        kubectl.chmod(0o755)
        state_path = Path(tmp) / "state.json"
        state_path.write_text(json.dumps(initial))
        env = {k: v for k, v in os.environ.items() if k not in OVERRIDES}
        env.update(FAKE_STATE=str(state_path), ORACLE_KUBECTL=str(kubectl), HOME_KUBECTL=str(kubectl),
                   HOME_FENCER_KUBECONFIG="/dev/null", PANTRY_POSTGRES_FENCE_TIMEOUT_SECONDS="3")
        env.update(extra_env or {})
        result = subprocess.run(["bash", str(script), *args], capture_output=True, text=True, env=env)
        return result, json.loads(state_path.read_text())


def live_oracle():
    deployments = {name: [1, 1] for name in WRITERS}
    deployments.update({"commands-cloudflared": [1, 1], "pantry-commands-site": [1, 1]})
    return state(statefulsets={"postgres-authority-standby-oracle-v2": 1},
                 pods={"postgres-authority-standby-oracle-v2-0": "pantry-bot-oracle"},
                 deployments=deployments)


def test_oracle_fence_stops_db_writers_and_connector_but_not_commands() -> None:
    """Catch a fence that leaves app writers or app-cloudflared serving, or touches commands."""
    result, final = run_fence(ORACLE, live_oracle())

    assert result.returncode == 0, result.stderr
    assert "fence_status=passed" in result.stdout
    assert final["statefulsets"]["postgres-authority-standby-oracle-v2"] == 0
    assert "postgres-authority-standby-oracle-v2-0" not in final["pods"]
    assert all(final["deployments"][name] == [0, 0] for name in WRITERS)
    assert final["deployments"]["commands-cloudflared"] == [1, 1]
    assert final["deployments"]["pantry-commands-site"] == [1, 1]
    touched = {arg for call in final["calls"] for arg in call}
    assert not touched & {"commands-cloudflared", "pantry-commands-site"}


def test_pod_delete_is_graceful_never_forced() -> None:
    """Catch --force, which removes the API object without proving the container stopped."""
    _, final = run_fence(ORACLE, live_oracle())
    deletes = [call for call in final["calls"] if call[0] == "delete"]

    assert deletes
    assert not any("--force" in call for call in final["calls"])


def test_pod_on_notready_node_fails_instead_of_passing() -> None:
    """Catch the home-failback case: a replicas=1 primary on NotReady chasebot must block."""
    result, _ = run_fence(HOME, state(
        statefulsets={"postgres-authority-home-failback": 1},
        pods={"postgres-authority-home-failback-0": "chasebot"},
        notready_nodes=["chasebot"],
    ))

    assert result.returncode == 1
    assert "not_fenced_within" in result.stderr
    assert "pod/postgres-authority-home-failback-0" in result.stderr
    assert "fence_status=passed" not in result.stdout


def test_absent_resources_count_as_fenced_and_are_named() -> None:
    """Catch a fence that fails on historical names or silently omits what it skipped."""
    result, _ = run_fence(ORACLE, state(statefulsets={"postgres-authority-standby-oracle-v2": 0}))

    assert result.returncode == 0, result.stderr
    absent = re.search(r"absent=(\S*)", result.stdout).group(1).split(",")
    assert "statefulset/postgres-authority-standby-reseed-v2" in absent
    assert "deployment/app-cloudflared" in absent


def test_forbidden_lookup_is_a_hard_failure_not_absent() -> None:
    """Catch an RBAC gap being read as 'not found' and reported as fenced."""
    result, _ = run_fence(HOME, state(
        statefulsets={"postgres-authority-home-v2": 1},
        forbidden=["postgres-authority-home-v2"],
    ))

    assert result.returncode == 1
    assert "lookup_failed:statefulset/postgres-authority-home-v2" in result.stderr
    assert "fence_status=passed" not in result.stdout


def test_deployment_that_never_drains_fails() -> None:
    """Catch trusting spec.replicas=0 while writer pods are still running."""
    result, _ = run_fence(ORACLE, state(
        statefulsets={"postgres-authority-standby-oracle-v2": 0},
        deployments={"pantry-chat-worker": [1, 1]},
        deploy_drains=False,
    ))

    assert result.returncode == 1
    assert "deployment/pantry-chat-worker" in result.stderr


def test_legacy_env_override_adds_to_the_list_instead_of_replacing_it() -> None:
    """Catch a stale PANTRY_STANDBY_STATEFULSET narrowing the fence to one dead name."""
    result, final = run_fence(ORACLE, state(statefulsets={
        "custom-sts": 1, "postgres-authority-standby-oracle-v2": 1,
    }), {"PANTRY_STANDBY_STATEFULSET": "custom-sts"})

    assert result.returncode == 0, result.stderr
    assert final["statefulsets"] == {"custom-sts": 0, "postgres-authority-standby-oracle-v2": 0}


def test_protected_connector_in_fence_list_is_refused_before_any_change() -> None:
    """Catch automation being pointed at the shared tunnel 59569621 connector."""
    result, final = run_fence(ORACLE, live_oracle(), {"PANTRY_FENCE_DEPLOYMENTS": "pantry-private-api cloudflared"})

    assert result.returncode == 1
    assert "protected_deployment_in_fence_list:cloudflared" in result.stderr
    assert not any(call[0] == "patch" for call in final["calls"])


def test_only_remote_network_silence_is_unreachable() -> None:
    """Catch refused/local failures being accepted as fenced-by-lease-expiry (exit 75)."""
    timeout = "Unable to connect to the server: dial tcp 100.84.89.87:6443: i/o timeout"
    refused = "Unable to connect to the server: dial tcp 100.84.89.87:6443: connect: connection refused"

    assert run_fence(HOME, state(probe_error=timeout))[0].returncode == 75
    assert run_fence(HOME, state(probe_error=refused))[0].returncode == 1
    assert run_fence(ORACLE, state(probe_error=timeout))[0].returncode == 1


def test_requires_explicit_confirmation() -> None:
    """Catch an accidental invocation fencing production."""
    result, final = run_fence(ORACLE, live_oracle(), args=())

    assert result.returncode == 1
    assert "explicit_confirmation_required" in result.stderr
    assert final["calls"] == []


def test_fence_list_is_the_complement_of_what_promotion_starts() -> None:
    """Catch fence and promotion lists drifting (a promoted app the fence never stops)."""
    fence = re.search(r'PANTRY_WRITER_DEPLOYMENTS="([^"]+)"', ORACLE.read_text()).group(1).split()
    tree = ast.parse((ROOT / "oracle_promoter.py").read_text())
    promotion = next(
        ast.literal_eval(node.value) for node in tree.body
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "ORACLE_PROMOTION_DEPLOYMENTS" for t in node.targets)
    )

    assert set(promotion) - {"pantry-commands-site"} == set(fence)


def test_dry_run_checks_reachability_and_rbac_without_writes() -> None:
    """Catch a preflight that mutates, or that hides an RBAC gap until the real fence."""
    ok, final = run_fence(HOME, state(statefulsets={"postgres-authority-home-v2": 1}), args=("--dry-run",))
    assert ok.returncode == 0, ok.stderr
    assert "fence_status=dry_run_ok" in ok.stdout
    assert final["statefulsets"]["postgres-authority-home-v2"] == 1
    assert not any(call[0] in ("patch", "delete") for call in final["calls"])

    denied, _ = run_fence(HOME, state(statefulsets={"postgres-authority-home-v2": 1},
                                      forbidden=["postgres-authority-home-v2"]), args=("--dry-run",))
    assert denied.returncode == 1
    assert "lookup_failed" in denied.stderr
