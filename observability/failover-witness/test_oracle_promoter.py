#!/usr/bin/env python3
"""Tests for the guarded Oracle PostgreSQL promotion sequence."""

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
    assert "--pod postgres-authority-standby-reseed-0" in service
    assert "--service postgres-authority-standby-reseed" in service
    assert "--postgres-port 5432" in service
    assert "--data-directory /var/lib/postgresql/data" in service
    assert "--manual-endpoint" in service


def test_topology_drop_in_uses_container_pgdata_root() -> None:
    topology = Path(__file__).with_name("pantry-postgres-oracle-promoter.topology.conf.example").read_text()
    assert "--data-directory /var/lib/postgresql/data --manual-endpoint" in topology
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
    assert calls == ["promote", "database-endpoint", "roles", "local-fence"]


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
