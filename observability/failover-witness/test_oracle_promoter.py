#!/usr/bin/env python3
"""Tests for the guarded Oracle PostgreSQL promotion sequence."""

import tempfile
from pathlib import Path

from oracle_promoter import (
    ActivationJournal,
    AuthorityLost,
    ORACLE_PROMOTION_DEPLOYMENTS,
    OraclePromoter,
    PromotionAdapters,
    _postgres_promote_command,
    _postgres_query_command,
    _primary_statefulset_patch,
)


def test_service_example_targets_live_oracle_standby_topology() -> None:
    service = Path(__file__).with_name("pantry-postgres-oracle-promoter.service.example").read_text()
    assert "--pod-namespace pantry-bot" in service
    assert "--service-namespace pantry-bot" in service
    assert "--pod postgres-authority-standby-home-v2-0" in service
    assert "--service postgres-authority-standby-home-v2" in service
    # --statefulset defaults to a retired resource name (see its
    # argparse default) and is used both by promote()'s statefulset patch
    # and by switch_endpoint()'s selector — a deployment that only updates
    # --pod/--service and forgets this flag silently mislabels the primary
    # Service's selector during a real promotion. Caught live 2026-09-21.
    assert "--statefulset postgres-authority-standby-home-v2" in service
    assert "--postgres-port 5432" in service
    assert "--data-directory /var/lib/postgresql/data" in service
    # Endpoint switching (repointing the stable Service selector and the
    # application's PANTRY_DATABASE_URL host) now runs automatically as part
    # of a real promotion; --manual-endpoint was a transitional flag used
    # while that path was still being hardened. See switch_endpoint().
    assert "--manual-endpoint" not in service


def test_topology_drop_in_uses_container_pgdata_root() -> None:
    topology = Path(__file__).with_name("pantry-postgres-oracle-promoter.topology.conf.example").read_text()
    assert "--pod postgres-authority-standby-home-v2-0" in topology
    assert "--service postgres-authority-standby-home-v2" in topology
    assert "--statefulset postgres-authority-standby-home-v2" in topology
    assert "--data-directory /var/lib/postgresql/data" in topology
    assert "--manual-endpoint" not in topology
    assert "/var/lib/postgresql/data/pgdata" not in topology


def test_promotion_endpoint_restart_covers_every_database_consumer() -> None:
    assert ORACLE_PROMOTION_DEPLOYMENTS == (
        "pantry-commands-site",
        "pantry-private-api",
        "pantry-private-site",
        "pantry-overlay-delivery",
        "pantry-twitch-gateway",
        "pantry-chat-worker",
        "pantry-twitch-dispatcher",
        "app-cloudflared",
    )


def test_postgres_promotion_runs_as_postgres_user() -> None:
    assert _postgres_promote_command("/var/lib/postgresql/data") == (
        "gosu",
        "postgres",
        "pg_ctl",
        "-D",
        "/var/lib/postgresql/data",
        "promote",
    )


def test_postgres_query_pins_live_oracle_database_topology() -> None:
    assert _postgres_query_command(5432, "pantry", "pantry", "select pg_is_in_recovery();") == (
        "sh", "-ec", "psql -h 127.0.0.1 -p 5432 -U pantry -d pantry -Atc 'select pg_is_in_recovery();'"
    )


def test_primary_statefulset_patch_removes_bootstrap_and_requires_writable_probe() -> None:
    import json

    operations = json.loads(_primary_statefulset_patch())
    assert operations[0] == {"op": "remove", "path": "/spec/template/spec/initContainers"}
    assert operations[1]["path"] == "/spec/template/spec/containers/0/readinessProbe/exec/command/2"
    assert "= f" in operations[1]["value"]


def test_held_authority_does_not_promote_or_enable_roles() -> None:
    calls: list[str] = []
    adapters = PromotionAdapters(
        acquire=lambda: None,
        is_primary=None,
        promote=lambda _token: calls.append("promote"),
        switch_endpoint=lambda: calls.append("endpoint"),
        enable_roles=lambda: calls.append("roles"),
        fence=lambda: calls.append("fence"),
        ready=lambda: True,
    )

    assert OraclePromoter(adapters).run_once() is False
    assert calls == []


def test_promotion_requires_shared_authority_and_orders_changes() -> None:
    calls: list[str] = []
    adapters = PromotionAdapters(
        acquire=lambda: {"epoch": 7, "token": "oracle-token"},
        is_primary=lambda: False,
        promote=lambda token: calls.append(f"promote:{token['epoch']}"),
        switch_endpoint=lambda: calls.append("endpoint"),
        enable_roles=lambda: calls.append("roles"),
        fence=lambda: calls.append("fence"),
        ready=lambda: True,
    )

    assert OraclePromoter(adapters).run_once() is True
    assert calls == ["promote:7", "endpoint", "roles"]


def test_old_writer_is_fenced_before_promotion() -> None:
    calls: list[str] = []
    adapters = PromotionAdapters(
        acquire=lambda: {"epoch": 10, "token": "oracle-token"},
        is_primary=lambda: False,
        promote=lambda _token: calls.append("promote"),
        switch_endpoint=lambda: calls.append("endpoint"),
        enable_roles=lambda: calls.append("roles"),
        fence=lambda: calls.append("fence"),
        renew=None,
        ready=lambda: True,
        fence_old_writer=lambda: calls.append("old-writer-fence"),
    )

    assert OraclePromoter(adapters).run_once() is True
    assert calls == ["old-writer-fence", "promote", "endpoint", "roles"]


def test_unverified_target_promotion_never_switches_endpoint_or_enables_roles() -> None:
    calls: list[str] = []
    adapters = PromotionAdapters(
        acquire=lambda: {"epoch": 12, "token": "oracle-token"},
        is_primary=lambda: False,
        promote=lambda _token: calls.append("promote"),
        switch_endpoint=lambda: calls.append("endpoint"),
        enable_roles=lambda: calls.append("roles"),
        fence=lambda: calls.append("local-fence"),
        ready=lambda: True,
        fence_old_writer=lambda: calls.append("old-writer-fence"),
        verify_promoted=lambda: False,
    )

    try:
        OraclePromoter(adapters).run_once()
    except RuntimeError as error:
        assert str(error) == "target PostgreSQL promotion could not be verified"
    else:
        raise AssertionError("unverified target must block routing")
    assert calls == ["old-writer-fence", "promote", "local-fence"]


def test_pantry_promoter_rejects_missing_old_writer_fence() -> None:
    import argparse

    args = argparse.Namespace(old_writer_fence_command=None)
    # The production adapter is deliberately fail-closed when the environment
    # has not supplied an out-of-band fence command.
    def invoke() -> None:
        if not args.old_writer_fence_command:
            raise RuntimeError("OLD_WRITER_FENCE_COMMAND is required before promotion")

    try:
        invoke()
    except RuntimeError as error:
        assert str(error) == "OLD_WRITER_FENCE_COMMAND is required before promotion"
    else:
        raise AssertionError("missing old-writer fencing must fail closed")


def test_failed_old_writer_fence_blocks_promotion_and_fences_locally() -> None:
    calls: list[str] = []

    def fence_old_writer() -> None:
        calls.append("old-writer-fence")
        raise RuntimeError("home writer could not be fenced")

    adapters = PromotionAdapters(
        acquire=lambda: {"epoch": 11, "token": "oracle-token"},
        is_primary=lambda: False,
        promote=lambda _token: calls.append("promote"),
        switch_endpoint=lambda: calls.append("endpoint"),
        enable_roles=lambda: calls.append("roles"),
        fence=lambda: calls.append("local-fence"),
        ready=lambda: True,
        fence_old_writer=fence_old_writer,
    )

    try:
        OraclePromoter(adapters).run_once()
    except RuntimeError as error:
        assert str(error) == "home writer could not be fenced"
    else:
        raise AssertionError("promotion must stop when old-writer fencing fails")
    assert calls == ["old-writer-fence", "local-fence"]


def test_renewal_loss_self_fences_after_promotion() -> None:
    calls: list[str] = []
    renewals = iter((True, False))
    adapters = PromotionAdapters(
        acquire=lambda: {"epoch": 8, "token": "oracle-token"},
        is_primary=lambda: False,
        promote=lambda _token: calls.append("promote"),
        switch_endpoint=lambda: calls.append("endpoint"),
        enable_roles=lambda: calls.append("roles"),
        fence=lambda: calls.append("fence"),
        renew=lambda _token: next(renewals),
        ready=lambda: True,
    )

    promoter = OraclePromoter(adapters)
    assert promoter.run_once() is True
    assert promoter.renew_or_fence() is False
    assert calls == ["promote", "endpoint", "roles", "fence"]


def test_lease_loss_before_activation_never_fences_another_site() -> None:
    calls: list[str] = []
    adapters = PromotionAdapters(
        acquire=lambda: {"epoch": 20, "token": "test"},
        is_primary=lambda: False,
        promote=lambda _: calls.append("promote"),
        switch_endpoint=lambda: calls.append("endpoint"),
        enable_roles=lambda: calls.append("roles"),
        fence=lambda: calls.append("local-fence"),
        renew=lambda _: False,
        fence_old_writer=lambda: calls.append("old-fence"),
    )
    try:
        OraclePromoter(adapters).run_once()
    except AuthorityLost:
        pass
    else:
        raise AssertionError("activation must fail before touching the old site")
    assert calls == ["local-fence"]


def test_lease_renewal_continues_during_slow_activation_and_loss_stops_routing() -> None:
    from threading import Event

    calls: list[str] = []
    slow_step_started = Event()
    fenced = Event()

    def renew(_token: dict) -> bool:
        return not slow_step_started.is_set()

    def promote(_token: dict) -> None:
        calls.append("promote")
        slow_step_started.set()
        assert fenced.wait(2), "renewal must run while the promotion operation is blocked"

    def fence() -> None:
        calls.append("local-fence")
        fenced.set()

    adapters = PromotionAdapters(
        acquire=lambda: {"epoch": 20, "token": "test"},
        is_primary=lambda: False,
        promote=promote,
        switch_endpoint=lambda: calls.append("endpoint"),
        enable_roles=lambda: calls.append("roles"),
        fence=fence,
        renew=renew,
    )
    try:
        OraclePromoter(adapters, renewal_interval=0.01).run_once()
    except AuthorityLost:
        pass
    else:
        raise AssertionError("lost authority must abort activation")
    assert calls == ["promote", "local-fence"]


def test_replication_failure_after_old_writer_fence_blocks_stale_database() -> None:
    calls: list[str] = []

    def replication() -> None:
        calls.append("replication")
        raise RuntimeError("standby WAL freshness is not established")

    adapters = PromotionAdapters(
        acquire=lambda: {"epoch": 20, "token": "test"},
        is_primary=lambda: False,
        promote=lambda _: calls.append("promote"),
        switch_endpoint=lambda: calls.append("endpoint"),
        enable_roles=lambda: calls.append("roles"),
        fence=lambda: calls.append("local-fence"),
        renew=lambda _: True,
        fence_old_writer=lambda: calls.append("old-fence"),
        check_replication=replication,
    )
    try:
        OraclePromoter(adapters).run_once()
    except RuntimeError as error:
        assert str(error) == "standby WAL freshness is not established"
    else:
        raise AssertionError("stale database must not become active")
    assert calls == ["old-fence", "replication", "local-fence"]


def test_routes_publish_only_after_all_service_health_checks() -> None:
    calls: list[str] = []
    adapters = PromotionAdapters(
        acquire=lambda: {"epoch": 20, "token": "test"},
        is_primary=lambda: False,
        promote=lambda _: calls.append("promote"),
        switch_endpoint=lambda: calls.append("database-endpoint"),
        enable_roles=lambda: calls.append("roles"),
        fence=lambda: calls.append("local-fence"),
        renew=lambda _: True,
        fence_old_writer=lambda: calls.append("old-fence"),
        verify_promoted=lambda: calls.append("database-verified") or True,
        check_replication=lambda: calls.append("replication"),
        verify_service=lambda: calls.append("service-verified"),
        publish_routes=lambda: calls.append("routes"),
    )
    assert OraclePromoter(adapters).run_once()
    assert calls == ["old-fence", "replication", "promote", "database-verified", "database-endpoint", "roles", "service-verified", "routes"]


def test_unhealthy_service_never_publishes_routes() -> None:
    """A post-promotion operational failure blocks routing but must not
    self-fence: the database is already verifiably promoted at this point,
    and destroying it over an app-tier health check would turn a recoverable
    problem into a second outage."""
    calls: list[str] = []

    def health() -> None:
        raise RuntimeError("dispatcher has no authority")

    adapters = PromotionAdapters(
        acquire=lambda: {"epoch": 20, "token": "test"},
        is_primary=lambda: False,
        promote=lambda _: calls.append("promote"),
        switch_endpoint=lambda: calls.append("database-endpoint"),
        enable_roles=lambda: calls.append("roles"),
        fence=lambda: calls.append("local-fence"),
        renew=lambda _: True,
        verify_service=health,
        publish_routes=lambda: calls.append("routes"),
    )
    try:
        OraclePromoter(adapters).run_once()
    except RuntimeError:
        pass
    else:
        raise AssertionError("unhealthy dispatcher must block public routing")
    assert calls == ["promote", "database-endpoint", "roles"]
    assert "local-fence" not in calls


def test_missing_production_hooks_fail_before_requesting_authority() -> None:
    from unittest.mock import patch
    import oracle_promoter

    with patch.dict("os.environ", {}, clear=True), patch("sys.argv", ["oracle_promoter.py", "--witness-url", "http://unused", "--secret", "test"]), patch.object(oracle_promoter, "_post") as post:
        try:
            oracle_promoter.run()
        except SystemExit as error:
            assert "OLD_WRITER_FENCE_COMMAND is required" in str(error)
        else:
            raise AssertionError("a controller missing fencing must not enter the election")
        post.assert_not_called()


def test_restart_receipt_rejects_wrong_epoch_database_and_fenced_state() -> None:
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "activation.json"
        journal = ActivationJournal(path)
        assert not journal.may_resume(20, "12345")
        journal.record(20, "12345", "promoting")
        assert ActivationJournal(path).may_resume(20, "12345")
        assert not journal.may_resume(21, "12345")
        assert not journal.may_resume(20, "98765")
        journal.record(20, "12345", "fenced")
        assert not journal.may_resume(20, "12345")


def test_generation_validation_precedes_old_writer_fencing() -> None:
    calls: list[str] = []

    def validate(_token: dict) -> None:
        calls.append("validate")
        raise RuntimeError("stale primary")

    adapters = PromotionAdapters(
        acquire=lambda: {"epoch": 20, "token": "test"},
        is_primary=lambda: False,
        promote=lambda _: calls.append("promote"),
        switch_endpoint=lambda: calls.append("database-endpoint"),
        enable_roles=lambda: calls.append("roles"),
        fence=lambda: calls.append("local-fence"),
        renew=lambda _: True,
        fence_old_writer=lambda: calls.append("old-fence"),
        validate_generation=validate,
    )
    try:
        OraclePromoter(adapters).run_once()
    except RuntimeError:
        pass
    else:
        raise AssertionError("unknown primary must never initiate another site's fencing")
    assert calls == ["validate", "local-fence"]


def test_resuming_a_validated_promotion_skips_redundant_fence_and_replication_steps() -> None:
    """A controller restart after a successful promotion must not re-run
    fence_old_writer/check_replication/promote.

    Reproduced live 2026-09-21: a controller restart between switch_endpoint
    and enable_roles correctly resumed past validate_generation (a matching
    activation receipt existed), but the code unconditionally re-ran
    check_replication next — which correctly rejects the target for no
    longer being a standby, since it was already promoted — turning a
    legitimate resume into a self-fence of the just-promoted primary.
    validate_generation now returns True for this case and run_once() skips
    straight to the post-promotion steps.
    """
    calls: list[str] = []

    adapters = PromotionAdapters(
        acquire=lambda: {"epoch": 20, "token": "test"},
        is_primary=lambda: True,
        promote=lambda _: calls.append("promote"),
        switch_endpoint=lambda: calls.append("endpoint"),
        enable_roles=lambda: calls.append("roles"),
        fence=lambda: calls.append("local-fence"),
        renew=lambda _: True,
        fence_old_writer=lambda: calls.append("old-fence"),
        check_replication=lambda: calls.append("replication-check"),
        validate_generation=lambda _token: True,
    )

    assert OraclePromoter(adapters).run_once() is True
    assert calls == ["endpoint", "roles"]
    assert "old-fence" not in calls
    assert "replication-check" not in calls
    assert "promote" not in calls
    assert "local-fence" not in calls


def test_journal_defaults_to_oracle_site() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "activation.json"
        journal = ActivationJournal(path)
        journal.record(7, "123", "active")
        loaded = journal.load()
        assert loaded is not None
        assert loaded["site"] == "oracle"
        assert journal.may_resume(7, "123") is True


def test_journal_for_canada_rejects_an_oracle_receipt() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "activation.json"
        oracle_journal = ActivationJournal(path, site="oracle")
        oracle_journal.record(7, "123", "active")
        
        canada_journal = ActivationJournal(path, site="canada")
        assert canada_journal.may_resume(7, "123") is False
        
        canada_journal.record(7, "123", "active")
        assert canada_journal.may_resume(7, "123") is True


def test_journal_failure_prevents_promotion() -> None:
    calls: list[str] = []

    def record(_token: dict, _phase: str) -> None:
        raise OSError("journal disk full")

    adapters = PromotionAdapters(
        acquire=lambda: {"epoch": 20, "token": "test"},
        is_primary=lambda: False,
        promote=lambda _: calls.append("promote"),
        switch_endpoint=lambda: calls.append("database-endpoint"),
        enable_roles=lambda: calls.append("roles"),
        fence=lambda: calls.append("local-fence"),
        renew=lambda _: True,
        record_progress=record,
    )
    try:
        OraclePromoter(adapters).run_once()
    except OSError:
        pass
    else:
        raise AssertionError("promotion needs a durable receipt before it can begin")
    assert calls == ["local-fence"]


def test_witness_error_on_controller_restart_self_fences() -> None:
    calls: list[str] = []

    def acquire() -> None:
        raise OSError("witness unreachable")

    adapters = PromotionAdapters(
        acquire=acquire,
        is_primary=lambda: True,
        promote=lambda _: calls.append("promote"),
        switch_endpoint=lambda: calls.append("endpoint"),
        enable_roles=lambda: calls.append("roles"),
        fence=lambda: calls.append("local-fence"),
    )
    try:
        OraclePromoter(adapters).run_once()
    except OSError:
        pass
    else:
        raise AssertionError("restarted primary must not run without authority")
    assert calls == ["local-fence"]


def test_primary_without_authority_fences_on_restart() -> None:
    calls: list[str] = []
    adapters = PromotionAdapters(
        acquire=lambda: None,
        is_primary=lambda: True,
        promote=lambda _token: calls.append("promote"),
        switch_endpoint=lambda: calls.append("endpoint"),
        enable_roles=lambda: calls.append("roles"),
        fence=lambda: calls.append("fence"),
        ready=lambda: True,
    )

    assert OraclePromoter(adapters).run_once() is False
    assert calls == ["fence"]


def test_unready_oracle_does_not_claim_shared_authority() -> None:
    calls: list[str] = []
    adapters = PromotionAdapters(
        acquire=lambda: calls.append("acquire") or {"epoch": 9, "token": "oracle-token"},
        is_primary=lambda: False,
        promote=lambda _token: calls.append("promote"),
        switch_endpoint=lambda: calls.append("endpoint"),
        enable_roles=lambda: calls.append("roles"),
        fence=lambda: calls.append("fence"),
        ready=lambda: False,
    )

    assert OraclePromoter(adapters).run_once() is False
    assert calls == []


if __name__ == "__main__":
    tests = [value for name, value in list(globals().items()) if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
    print(f"test_oracle_promoter: {len(tests)} tests passed")


def test_local_fence_keeps_a_positively_proven_standby_streaming() -> None:
    """Catch a failed pre-promotion attempt scaling its own standby to 0 (seen live
    2026-09-22: it blocked every retry and dropped the site out of the replica set)."""
    from oracle_promoter import select_local_fence
    assert select_local_fence("t", "writer", "standby") == "standby"


def test_local_fence_takes_the_database_unless_recovery_is_proven() -> None:
    """Catch an ambiguous/failed recovery probe being treated as 'safe standby'."""
    from oracle_promoter import select_local_fence
    for answer in ("f", None, "", "error", "t\n", "T"):
        assert select_local_fence(answer, "writer", "standby") == "writer", answer
    assert select_local_fence("t", "writer", None) == "writer"
    assert select_local_fence("t", "writer", "") == "writer"


def _gate_promoter(gate_open: bool, primary: bool, calls: list):
    from oracle_promoter import OraclePromoter, PromotionAdapters
    return OraclePromoter(PromotionAdapters(
        acquire=lambda: calls.append("acquire") or None,
        is_primary=lambda: primary,
        promote=lambda _t: calls.append("promote"),
        switch_endpoint=lambda: None, enable_roles=lambda: None,
        fence=lambda: calls.append("fence"),
        may_acquire=lambda: gate_open,
    ))


def test_closed_gate_never_asks_the_witness() -> None:
    """Catch a lower-priority site racing a higher-priority one for the lease."""
    calls = []
    assert _gate_promoter(False, False, calls).run_once() is False
    assert calls == []


def test_primary_skips_the_gate_and_asks_the_witness() -> None:
    """Catch a dead follower (gate closed) making a healthy primary fence itself."""
    calls = []
    try:
        _gate_promoter(False, True, calls).run_once()
    except Exception:
        pass
    assert calls[0] == "acquire"


def test_open_gate_proceeds_to_acquire() -> None:
    calls = []
    _gate_promoter(True, False, calls).run_once()
    assert calls == ["acquire"]


def test_resume_after_lapsed_lease_only_at_exactly_the_next_epoch(tmp_path) -> None:
    """Catch a promoter restart >TTL fencing the healthy sole primary (needless
    failover), without letting a primary resume after ANOTHER site held authority."""
    from oracle_promoter import ActivationJournal
    j = ActivationJournal(tmp_path / "a.json", site="home")
    j.record(87, "S", "active")
    assert j.may_resume(87, "S")
    assert j.may_resume(88, "S")            # nobody else was granted 88
    assert not j.may_resume(89, "S")        # someone held 88 in between
    assert not j.may_resume(88, "OTHER")
    j.record(87, "S", "promoted")           # incomplete activation: exact epoch only
    assert j.may_resume(87, "S") and not j.may_resume(88, "S")
    ActivationJournal(tmp_path / "a.json", site="oracle")
    assert not ActivationJournal(tmp_path / "a.json", site="oracle").may_resume(87, "S")


def _active_promoter(renew_ok=True):
    from oracle_promoter import OraclePromoter, PromotionAdapters
    calls = []
    pr = OraclePromoter(PromotionAdapters(
        acquire=lambda: None, is_primary=lambda: True, promote=lambda _t: None,
        switch_endpoint=lambda: None, enable_roles=lambda: None,
        fence=lambda: calls.append("fence"), renew=lambda _t: renew_ok,
    ), renewal_interval=0.05)
    pr.promoted = True; pr.token = {"epoch": 90, "token": "x"}
    return pr, calls


def test_hand_back_only_when_check_passes_and_execute_succeeds() -> None:
    """Catch yielding without an eligible target, or on an aborted drain."""
    from oracle_promoter import attempt_hand_back
    pr, _ = _active_promoter()
    y = []
    assert attempt_hand_back(pr, lambda: False, lambda: 0, lambda: y.append(1)) is False
    assert attempt_hand_back(pr, lambda: True, lambda: 2, lambda: y.append(1)) is False
    assert y == []
    assert attempt_hand_back(pr, lambda: True, lambda: 0, lambda: y.append(1)) is True
    assert y == [1]


def test_hand_back_never_runs_without_authority() -> None:
    """Catch a non-holder (or unpromoted site) initiating a hand-back."""
    from oracle_promoter import attempt_hand_back
    pr, _ = _active_promoter()
    pr.promoted = False
    ran = []
    assert attempt_hand_back(pr, lambda: True, lambda: ran.append(1) or 0, lambda: None) is False
    assert ran == []


def test_lease_is_renewed_during_a_slow_drain_and_loss_fences() -> None:
    """Catch a drain longer than the TTL lapsing the lease silently."""
    import time as _t
    from oracle_promoter import attempt_hand_back, AuthorityLost
    pr, calls = _active_promoter()
    renewals = []
    pr.adapters.renew = lambda _t2: renewals.append(1) or True
    assert attempt_hand_back(pr, lambda: True, lambda: (_t.sleep(0.3), 0)[1], lambda: None) is True
    assert len(renewals) >= 3
    pr2, calls2 = _active_promoter(renew_ok=True)
    state = {"n": 0}
    def flaky(_t3):
        state["n"] += 1
        return state["n"] < 3
    pr2.adapters.renew = flaky
    try:
        attempt_hand_back(pr2, lambda: True, lambda: (_t.sleep(0.4), 0)[1], lambda: None)
        raised = False
    except AuthorityLost:
        raised = True
    assert raised and "fence" in calls2


def test_watchdog_releases_only_after_sustained_primary_loss() -> None:
    """Catch a promoter renewing forever over a dead primary (no failover), and
    a brief pod restart causing a needless failover."""
    from oracle_promoter import PrimaryWatchdog
    now = {"t": 0.0}
    w = PrimaryWatchdog(60, clock=lambda: now["t"])
    assert not w.should_release(False)
    now["t"] = 30; assert not w.should_release(False)
    now["t"] = 40; assert not w.should_release(True)      # recovered: clock resets
    now["t"] = 50; assert not w.should_release(False)
    now["t"] = 109; assert not w.should_release(False)
    now["t"] = 110; assert w.should_release(False)
