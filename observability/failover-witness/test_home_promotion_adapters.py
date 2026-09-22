#!/usr/bin/env python3
"""Contracts for the Home promoter's fence adapters (#191)."""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml


ROOT = Path(__file__).parent


def lists(script: str) -> tuple[set[str], set[str]]:
    text = (ROOT / script).read_text()
    sts = re.search(r'_PANTRY_STATEFULSETS="([^"]+)"', text).group(1).split()
    dep = re.search(r'PANTRY_WRITER_DEPLOYMENTS="([^"]+)"', text).group(1).split()
    return set(sts), set(dep)


def test_remote_and_local_fences_of_a_site_cover_the_same_writers() -> None:
    """Catch the direct (remote) fence drifting from the site's own local fence."""
    assert lists("fence-oracle-direct.sh") == lists("fence-oracle-postgres-local.sh")
    assert lists("fence-home-postgres-local.sh") == lists("fence-home-direct-from-oracle.sh")


def test_oracle_fencer_role_grants_exactly_the_direct_fence_targets() -> None:
    """Catch an Oracle fence target missing from RBAC (Forbidden at 2am) or grant creep."""
    docs = {d["kind"]: d for d in yaml.safe_load_all((ROOT / "pantry-postgres-fencer-oracle-rbac.yaml").read_text())}
    role = docs["Role"]
    by = lambda r: {n for rule in role["rules"] if r in rule["resources"] for n in rule["resourceNames"]}
    sts, dep = lists("fence-oracle-direct.sh")
    assert by("statefulsets") == sts
    assert by("pods") == {f"{s}-0" for s in sts}
    assert by("deployments") == dep
    assert not (by("deployments") & {"cloudflared", "commands-cloudflared", "pantry-commands-site"})
    assert {v for rule in role["rules"] for v in rule["verbs"]} <= {"get", "patch", "delete"}
    assert docs["Secret"]["type"] == "kubernetes.io/service-account-token"


def _run_home_composite(oracle_rc: int, canada_rc: int):
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        shutil.copy(ROOT / "fence-old-writers-from-home.sh", tmp_path)
        for name, rc in (("fence-oracle-direct.sh", oracle_rc), ("fence-canada-from-oracle.sh", canada_rc)):
            stub = tmp_path / name
            stub.write_text(f"#!/usr/bin/env bash\nexit {rc}\n")
            stub.chmod(0o755)
        env = {**os.environ, "PANTRY_UNREACHABLE_GRACE_SECONDS": "0"}
        return subprocess.run(["bash", str(tmp_path / "fence-old-writers-from-home.sh"), "--confirm"],
                              capture_output=True, text=True, env=env)


def test_home_composite_fences_oracle_and_canada_with_the_same_policy() -> None:
    """Catch the Home composite accepting a failed reachable fence (split-brain)."""
    assert "oracle=verified canada=verified" in _run_home_composite(0, 0).stdout
    assert "oracle=lease_expiry canada=verified" in _run_home_composite(75, 0).stdout
    for rcs in ((1, 0), (0, 1), (75, 2), (3, 75)):
        result = _run_home_composite(*rcs)
        assert result.returncode != 0, rcs
        assert "old_writer_fence=verified" not in result.stdout


def test_canada_fence_gate_executes_only_fixed_paths() -> None:
    """Catch the forced-command gate running caller-supplied paths or arguments."""
    text = (ROOT / "canada" / "canada-fence-gate.ps1").read_text()
    assert "SSH_ORIGINAL_COMMAND" in text
    assert "Invoke-Expression" not in text and "iex " not in text
    assert "& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $fence -ConfirmFence" in text
    assert "canada_fence_gate=denied" in text


def test_env_files_quote_every_value_containing_spaces() -> None:
    """Catch an unquoted multi-word value: harmless to systemd, but sourcing the
    file in a shell EXECUTES it (2026-09-22: this ran a fence on the live primary)."""
    import glob
    offenders = []
    for path in glob.glob(str(ROOT / "**" / "*.env*"), recursive=True):
        for n, line in enumerate(Path(path).read_text().splitlines(), 1):
            m = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line)
            if m and " " in m.group(2) and not re.fullmatch(r'"[^"]*"|\'[^\']*\'', m.group(2)):
                offenders.append(f"{path}:{n}")
    assert not offenders, offenders
