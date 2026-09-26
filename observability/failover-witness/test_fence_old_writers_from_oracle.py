#!/usr/bin/env python3
"""Static contract tests for the composite Oracle old-writer fence."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).parent


def test_composite_requires_explicit_mode_and_calls_both_fences() -> None:
    text = (ROOT / "fence-old-writers-from-oracle.sh").read_text()
    assert 'explicit_confirmation_required' in text
    # Home's PostgreSQL now runs on minecraftmachine, the k3s control-plane
    # node itself, which Oracle can reach directly — no more ChaseBot forced-
    # command/GCP relay needed for this direction. The legacy transport
    # (fence-home-from-oracle.sh, tested separately below) remains for
    # documentation/history but is no longer wired into the live promotion
    # path.
    assert 'fence-home-direct-from-oracle.sh' in text
    assert 'fence-canada-from-oracle.sh' in text
    assert 'old_writer_fence=verified' in text


def _run_composite(home_rc: int, canada_rc: int, mode: str = "--confirm"):
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        shutil.copy(ROOT / "fence-old-writers-from-oracle.sh", tmp_path)
        for name, rc in (("fence-home-direct-from-oracle.sh", home_rc), ("fence-canada-from-oracle.sh", canada_rc)):
            stub = tmp_path / name
            stub.write_text(f"#!/usr/bin/env bash\nexit {rc}\n")
            stub.chmod(0o755)
        env = {**os.environ, "PANTRY_UNREACHABLE_GRACE_SECONDS": "0"}
        return subprocess.run(["bash", str(tmp_path / "fence-old-writers-from-oracle.sh"), mode],
                              capture_output=True, text=True, env=env)


def test_both_sites_positively_fenced() -> None:
    result = _run_composite(0, 0)
    assert result.returncode == 0
    assert "home=verified canada=verified" in result.stdout


def test_unreachable_site_is_accepted_as_lease_expiry() -> None:
    result = _run_composite(0, 75)
    assert result.returncode == 0
    assert "home=verified canada=lease_expiry" in result.stdout
    result = _run_composite(75, 75)
    assert result.returncode == 0
    assert "home=lease_expiry canada=lease_expiry" in result.stdout


def test_reachable_fence_failure_blocks_promotion() -> None:
    """Catch the split-brain regression where a failed fence exited 0.

    A draft of this composite read rc from `$?` inside `if ! cmd`, which is
    always 0, so every failed fence - including a live, reachable, unfenced
    writer - reported success and the promoter would have promoted on top
    of it.
    """
    for home_rc, canada_rc in ((0, 1), (3, 0), (1, 75), (75, 2)):
        result = _run_composite(home_rc, canada_rc)
        assert result.returncode != 0, (home_rc, canada_rc, result.stdout)
        assert "old_writer_fence=verified" not in result.stdout


def _run_composite_recording(home_rc: dict[str, int], canada_rc: dict[str, int]):
    """Run the composite with stubs whose exit code depends on the mode.

    Returns (CompletedProcess, [(site, mode), ...]) in call order, so a test
    can assert what was actually fenced rather than only the exit code.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        shutil.copy(ROOT / "fence-old-writers-from-oracle.sh", tmp_path)
        log = tmp_path / "calls.log"
        for name, site, codes in (
            ("fence-home-direct-from-oracle.sh", "home", home_rc),
            ("fence-canada-from-oracle.sh", "canada", canada_rc),
        ):
            stub = tmp_path / name
            stub.write_text(
                "#!/usr/bin/env bash\n"
                f'echo "{site} $1" >> "{log}"\n'
                f'case "$1" in\n'
                f'  --dry-run) exit {codes["--dry-run"]} ;;\n'
                f'  --confirm) exit {codes["--confirm"]} ;;\n'
                "esac\n"
            )
            stub.chmod(0o755)
        env = {**os.environ, "PANTRY_UNREACHABLE_GRACE_SECONDS": "0"}
        result = subprocess.run(
            ["bash", str(tmp_path / "fence-old-writers-from-oracle.sh"), "--confirm"],
            capture_output=True, text=True, env=env,
        )
        calls = [tuple(line.split()) for line in log.read_text().splitlines()] if log.exists() else []
        return result, calls


def test_a_later_sites_failure_fences_nobody() -> None:
    """Catch the livelock that caused the 2026-09-23 outage.

    Canada's fence script exited 1 (its Docker daemon was down). Because the
    composite fences home before it attempts canada, Home was fenced on every
    pass and then the promoter crashed on Canada, was restarted by systemd,
    and did it again - 14 times, never completing a promotion, with no way to
    unfence Home. Probing every transport first makes that failure cost
    nothing.
    """
    ok = {"--dry-run": 0, "--confirm": 0}
    broken = {"--dry-run": 1, "--confirm": 1}
    result, calls = _run_composite_recording(ok, broken)

    assert result.returncode != 0
    assert "phase=probe" in result.stderr and "nothing fenced" in result.stderr
    assert ("home", "--confirm") not in calls, (
        "home was fenced even though canada's fence could never succeed: "
        f"calls={calls}"
    )
    assert calls == [("home", "--dry-run"), ("canada", "--dry-run")]


def test_probe_success_still_fences_every_site() -> None:
    ok = {"--dry-run": 0, "--confirm": 0}
    result, calls = _run_composite_recording(ok, ok)
    assert result.returncode == 0
    assert "home=verified canada=verified" in result.stdout
    assert ("home", "--confirm") in calls and ("canada", "--confirm") in calls


def test_probe_accepts_an_unreachable_site() -> None:
    """Exit 75 in the probe must not block promotion.

    A fully dark site is accepted as fenced by witness-lease expiry; the
    probe must not turn that documented policy back into a hard failure.
    """
    ok = {"--dry-run": 0, "--confirm": 0}
    dark = {"--dry-run": 75, "--confirm": 75}
    result, calls = _run_composite_recording(ok, dark)
    assert result.returncode == 0, result.stderr
    assert "home=verified canada=lease_expiry" in result.stdout
    assert ("home", "--confirm") in calls


def _run_home_fence(probe_stderr: str):
    with tempfile.TemporaryDirectory() as tmp:
        kubectl = Path(tmp) / "kubectl"
        kubectl.write_text("#!/usr/bin/env bash\ncat >&2 <<'EOF'\n" + probe_stderr + "\nEOF\nexit 1\n")
        kubectl.chmod(0o755)
        env = {**os.environ, "HOME_KUBECTL": str(kubectl), "HOME_FENCER_KUBECONFIG": "/dev/null"}
        return subprocess.run(["bash", str(ROOT / "fence-home-direct-from-oracle.sh"), "--confirm"],
                              capture_output=True, text=True, env=env)


def test_home_network_silence_is_unreachable() -> None:
    for message in (
        'Unable to connect to the server: dial tcp 100.84.89.87:6443: i/o timeout',
        'Unable to connect to the server: dial tcp 100.84.89.87:6443: connect: no route to host',
        'Unable to connect to the server: context deadline exceeded',
    ):
        assert _run_home_fence(message).returncode == 75, message


def test_home_refused_forbidden_or_other_errors_stay_hard_failures() -> None:
    """Refused means the host is up (k3s containers outlive the API)."""
    for message in (
        'Unable to connect to the server: dial tcp 100.84.89.87:6443: connect: connection refused',
        'error: You must be logged in to the server (Unauthorized)',
        'Error from server (Forbidden): forbidden',
    ):
        result = _run_home_fence(message)
        assert result.returncode == 1, (message, result.returncode)


def test_canada_only_treats_network_silence_as_unreachable() -> None:
    text = (ROOT / "fence-canada-from-oracle.sh").read_text()
    assert "exit 75" in text
    assert "timed out|no route to host|network is unreachable" in text
    assert "refused|permission denied|host key|authentication" in text


def test_home_transport_is_identity_pinned_and_no_arbitrary_command() -> None:
    text = (ROOT / "fence-home-from-oracle.sh").read_text()
    assert 'GCP_HOST="136.113.178.106"' in text
    assert 'GCP_USER="sa_105559435168833655240"' in text
    assert 'StrictHostKeyChecking=yes' in text
    assert 'REMOTE_FENCE="/usr/local/lib/failover-witness/gcp-fence-home-writer.sh"' in text
    assert 'sudo -n "$REMOTE_FENCE"' in text


def test_home_transport_uses_independent_gcp_forced_fence() -> None:
    """Catch a transport that bypasses the ChaseBot lease-renewer fence."""
    text = (ROOT / "fence-home-from-oracle.sh").read_text()

    assert "136.113.178.106" in text
    assert "gcp-witness-oracle" in text
    assert "gcp-fence-home-writer.sh" in text
    assert "100.84.89.87" not in text
    assert "fence-pantry-postgres.sh" not in text
