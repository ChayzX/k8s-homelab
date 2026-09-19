#!/usr/bin/env python3
from pathlib import Path


def test_service_check_is_fail_closed_and_checks_writable_authority() -> None:
    text = Path(__file__).with_name("oracle-service-check.sh").read_text()
    assert "database_not_primary" in text
    assert "database_read_only" in text
    assert "authority_endpoint_missing" in text
    assert "deployment_not_ready" in text
    assert "transaction_read_only" in text
