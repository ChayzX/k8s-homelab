import importlib.util
import json
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("routing_proof.py")
spec = importlib.util.spec_from_file_location("routing_proof", MODULE_PATH)
routing_proof = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = routing_proof
spec.loader.exec_module(routing_proof)


class FakeResponse:
    def __init__(self, status=200, body=b"{}", headers=None):
        self.status = status
        self._body = body
        self.headers = headers or {"Content-Type": "application/json"}

    def read(self, limit=-1):
        return self._body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_probe_records_timing_and_response_metadata():
    result = routing_proof.probe_url(
        "https://origin.example/api/public/commands",
        opener=lambda _request, timeout: FakeResponse(body=b'{"commands":[]}'),
        now=lambda: 1700000000.25,
    )

    assert result["ok"] is True
    assert result["status"] == 200
    assert result["started_at"] == "2023-11-14T22:13:20.250000Z"
    assert result["finished_at"] == result["started_at"]
    assert result["elapsed_ms"] == 0.0
    assert result["content_type"] == "application/json"


def test_run_proof_keeps_origin_health_separate_from_public_route_claim():
    calls = []

    def opener(request, timeout):
        calls.append(request.full_url)
        return FakeResponse(body=b'{"commands":[]}')

    evidence = routing_proof.run_proof(
        home_origin="https://home.example",
        oracle_origin="https://oracle.example",
        public_url="https://commands.greeniespantry.uk",
        opener=opener,
        now=lambda: 1700000000.25,
        convergence_assumption_seconds=90,
    )

    assert calls == [
        "https://home.example/",
        "https://home.example/api/public/commands",
        "https://oracle.example/",
        "https://oracle.example/api/public/commands",
        "https://commands.greeniespantry.uk/",
        "https://commands.greeniespantry.uk/api/public/commands",
    ]
    assert evidence["origins"]["home"]["ok"] is True
    assert evidence["origins"]["oracle"]["ok"] is True
    assert evidence["public_route"]["ok"] is True
    assert evidence["cloudflare_failover"]["status"] == "not_verified"
    assert evidence["cloudflare_failover"]["reason"] == "no external route transition was observed"
    assert evidence["assumptions"]["route_convergence_seconds"] == 90
    assert evidence["assumptions"]["measured"] is False


def test_failed_oracle_does_not_mask_healthy_home():
    def opener(request, timeout):
        if "oracle.example" in request.full_url:
            return FakeResponse(status=503, body=b"unavailable", headers={"Content-Type": "text/plain"})
        return FakeResponse(body=b'{"commands":[]}')

    evidence = routing_proof.run_proof(
        home_origin="https://home.example",
        oracle_origin="https://oracle.example",
        public_url="https://commands.greeniespantry.uk",
        opener=opener,
        now=lambda: 1700000000.25,
    )

    assert evidence["origins"]["home"]["ok"] is True
    assert evidence["origins"]["oracle"]["ok"] is False
    assert evidence["ok"] is False


def test_route_marker_can_verify_current_external_target_without_claiming_failover():
    evidence = routing_proof.run_proof(
        home_origin="https://home.example",
        oracle_origin="https://oracle.example",
        public_url="https://commands.greeniespantry.uk",
        opener=lambda request, timeout: FakeResponse(
            headers={"Content-Type": "text/html", "X-PantryBot-Origin": "oracle"}
        ),
        now=lambda: 1700000000.25,
        public_origin_header="X-PantryBot-Origin",
        expected_public_origin="oracle",
    )

    assert evidence["public_route"]["target_observation"] == {
        "header": "X-PantryBot-Origin",
        "value": "oracle",
        "expected": "oracle",
        "verified": True,
    }
    assert evidence["cloudflare_failover"]["status"] == "not_verified"


def test_external_transition_evidence_is_the_only_failover_verification_path():
    result = routing_proof.validate_transition_evidence(
        {
            "public_hostname": "commands.greeniespantry.uk",
            "observations": [
                {
                    "observed_at": "2026-09-10T20:00:00Z",
                    "target": "home",
                    "externally_observed": True,
                },
                {
                    "observed_at": "2026-09-10T20:05:00Z",
                    "target": "oracle",
                    "externally_observed": True,
                },
            ],
        },
        public_hostname="commands.greeniespantry.uk",
    )

    assert result == {
        "status": "verified",
        "observations": 2,
        "first_target": "home",
        "last_target": "oracle",
    }


test_probe_records_timing_and_response_metadata()
test_run_proof_keeps_origin_health_separate_from_public_route_claim()
test_failed_oracle_does_not_mask_healthy_home()
test_route_marker_can_verify_current_external_target_without_claiming_failover()
test_external_transition_evidence_is_the_only_failover_verification_path()
print("test_routing_proof: all assertions passed")
