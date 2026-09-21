#!/usr/bin/env python3
"""Container-name contracts for the live Canada fence script."""

import re
from pathlib import Path


HERE = Path(__file__).parent


def _powershell_string_array(script: str, variable: str) -> list[str]:
    match = re.search(rf"\${variable}\s*=\s*@\(([^)]*)\)", script, re.DOTALL)
    assert match, f"${variable} array not found"
    return re.findall(r"'([^']+)'", match.group(1))


def test_fence_script_targets_are_plain_docker_run_names_not_compose_labels() -> None:
    """Catch reintroducing a compose-project/service label filter.

    2026-09-21: a same-day rewrite switched fence-canada-writer.ps1 to derive
    container names from `com.docker.compose.project`/`.service` labels,
    on the false premise that a docker-compose.canada.yml governs production.
    Live `docker ps -a` showed no Canada container carries any
    com.docker.compose.* label — production containers are started by plain
    `docker run --name pantrybot-canada-prod-<role> ...` in
    start-canada-production.ps1. The label-based rewrite would have found
    zero containers and refused every real fence. Caught and reverted before
    any real fence used it; this test keeps it from silently coming back.
    """
    script = (HERE / "fence-canada-writer.ps1").read_text()
    assert "--filter" not in script and "label=" not in script, (
        "fence-canada-writer.ps1 must not filter containers by label: "
        "live Canada production containers are unlabeled `docker run` containers"
    )


def test_fence_script_container_list_matches_the_live_authority_gate() -> None:
    """Catch the fence and the authority gate silently drifting apart.

    Both scripts independently enumerate Canada's production application
    containers. authority-gate.ps1 is the live, continuously-running
    supervisor (it starts these containers when Canada holds primary
    authority); fence-canada-writer.ps1 must stop the exact same set, or a
    renamed/added role could keep writing after a "successful" fence.
    """
    fence_script = (HERE / "fence-canada-writer.ps1").read_text()
    gate_script = (HERE / "canada" / "authority-gate.ps1").read_text()

    fence_mutating = set(_powershell_string_array(fence_script, "mutating"))
    assert fence_mutating == {
        "pantrybot-canada-prod-worker",
        "pantrybot-canada-prod-dispatcher",
        "pantrybot-canada-prod-overlay",
        "pantrybot-canada-prod-api",
        "pantrybot-canada-prod-gateway",
        "pantrybot-canada-prod-private",
        "pantrybot-canada-prod-public",
    }

    gate_containers = set(_powershell_string_array(gate_script, "appContainers"))
    assert fence_mutating == gate_containers, (
        "fence-canada-writer.ps1's $mutating list and authority-gate.ps1's "
        "$appContainers list have drifted apart"
    )


def test_fence_script_postgres_container_default_matches_authority_gate() -> None:
    fence_script = (HERE / "fence-canada-writer.ps1").read_text()
    match = re.search(r"\[string\]\$PostgresContainer\s*=\s*'([^']+)'", fence_script)
    assert match, "fence-canada-writer.ps1 must default $PostgresContainer"
    assert match.group(1) == "pantrybot-canada-postgres"


if __name__ == "__main__":
    import sys

    failures = 0
    for name, value in list(globals().items()):
        if name.startswith("test_") and callable(value):
            try:
                value()
                print(f"PASS {name}")
            except AssertionError as error:
                failures += 1
                print(f"FAIL {name}: {error}")
    sys.exit(1 if failures else 0)
