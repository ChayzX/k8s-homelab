#!/usr/bin/env python3
"""Read-only proof harness for PantryBot's public routing contract.

This probes both site origins directly and the friendly public hostname.  It
does not change DNS, Cloudflare, Kubernetes, or any live service.  A healthy
public response proves only that the public route answered; it does not prove
that Cloudflare failed over between origins.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


DEFAULT_TIMEOUT_SECONDS = 12
DEFAULT_DETECTION_ASSUMPTION_SECONDS = 60
DEFAULT_CONVERGENCE_ASSUMPTION_SECONDS = 90
USER_AGENT = "pantrybot-routing-proof/1"


@dataclass(frozen=True)
class Route:
    name: str
    path: str
    expected_statuses: frozenset[int]
    expected_content_type: str | None = None


ROUTES = (
    Route("viewer", "/", frozenset({200})),
    Route("command_manifest", "/api/public/commands", frozenset({200}), "application/json"),
)

# These paths must not expose the private OAuth/moderator surface when the
# viewer-friendly hostname is routed to the standalone commands service.
PUBLIC_ONLY_ROUTES = (
    Route("operator_login", "/login/broadcaster", frozenset({404})),
)


def utc_timestamp(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, timezone.utc).isoformat().replace("+00:00", "Z")


def origin_url(origin: str, path: str) -> str:
    return urllib.parse.urljoin(origin.rstrip("/") + "/", path.lstrip("/"))


def _header_value(headers: Any, name: str) -> str | None:
    value = headers.get(name)
    return str(value) if value is not None else None


def probe_url(
    url: str,
    *,
    expected_statuses: frozenset[int] = frozenset({200}),
    expected_content_type: str | None = None,
    capture_header: str | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
    now: Callable[[], float] = time.time,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Probe one URL and return non-secret, timestamped evidence."""
    started = now()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with opener(request, timeout=timeout) as response:
            status = response.status
            content_type = _header_value(response.headers, "Content-Type")
            captured = (
                _header_value(response.headers, capture_header)
                if capture_header
                else None
            )
            response.read(4096)
        error = None
    except urllib.error.HTTPError as exc:
        status = exc.code
        content_type = _header_value(exc.headers, "Content-Type")
        captured = (
            _header_value(exc.headers, capture_header) if capture_header else None
        )
        error = None
    except Exception as exc:  # noqa: BLE001 - a proof report must include network failures
        status = None
        content_type = None
        captured = None
        error = f"{type(exc).__name__}: {exc}"
    finished = now()
    result: dict[str, Any] = {
        "url": url,
        "ok": error is None
        and status in expected_statuses
        and (
            expected_content_type is None
            or (content_type or "").split(";", 1)[0].strip().lower()
            == expected_content_type.lower()
        ),
        "status": status,
        "content_type": content_type,
        "started_at": utc_timestamp(started),
        "finished_at": utc_timestamp(finished),
        "elapsed_ms": round(max(0.0, finished - started) * 1000, 1),
    }
    if capture_header:
        result["captured_header"] = {"name": capture_header, "value": captured}
    if error:
        result["error"] = error
    if status not in expected_statuses and error is None:
        result["reason"] = f"unexpected HTTP status {status}; expected {sorted(expected_statuses)}"
    elif expected_content_type and not result["ok"] and error is None:
        result["reason"] = (
            f"unexpected content type {content_type!r}; expected {expected_content_type}"
        )
    return result


def _probe_site(
    name: str,
    origin: str,
    *,
    opener: Callable[..., Any],
    now: Callable[[], float],
    timeout: int,
    capture_header: str | None = None,
) -> dict[str, Any]:
    routes = {}
    for route in ROUTES:
        routes[route.name] = probe_url(
            origin_url(origin, route.path),
            expected_statuses=route.expected_statuses,
            expected_content_type=route.expected_content_type,
            opener=opener,
            now=now,
            timeout=timeout,
            capture_header=capture_header,
        )
    return {
        "origin": origin,
        "routes": routes,
        "ok": all(result["ok"] for result in routes.values()),
    }


def _public_route(
    public_url: str,
    *,
    opener: Callable[..., Any],
    now: Callable[[], float],
    timeout: int,
    public_origin_header: str | None,
    expected_public_origin: str | None,
) -> dict[str, Any]:
    routes = {}
    for route in ROUTES:
        routes[route.name] = probe_url(
            origin_url(public_url, route.path),
            expected_statuses=route.expected_statuses,
            expected_content_type=route.expected_content_type,
            opener=opener,
            now=now,
            timeout=timeout,
            capture_header=public_origin_header,
        )
    for route in PUBLIC_ONLY_ROUTES:
        routes[route.name] = probe_url(
            origin_url(public_url, route.path),
            expected_statuses=route.expected_statuses,
            expected_content_type=route.expected_content_type,
            opener=opener,
            now=now,
            timeout=timeout,
            capture_header=public_origin_header,
        )
    result: dict[str, Any] = {
        "url": public_url,
        "routes": routes,
        "ok": all(route["ok"] for route in routes.values()),
    }
    if public_origin_header and expected_public_origin:
        observed = routes["viewer"].get("captured_header", {}).get("value")
        result["target_observation"] = {
            "header": public_origin_header,
            "value": observed,
            "expected": expected_public_origin,
            "verified": observed == expected_public_origin,
        }
        result["ok"] = result["ok"] and observed == expected_public_origin
    return result


def validate_transition_evidence(
    evidence: dict[str, Any], *, public_hostname: str
) -> dict[str, Any]:
    """Validate externally collected before/after route observations.

    The evidence must explicitly state that the observations were external and
    must show both site targets in chronological order.  This validates a
    supplied artifact; it never creates one by probing a single route.
    """
    observations = evidence.get("observations")
    if evidence.get("public_hostname") != public_hostname:
        raise ValueError("transition evidence hostname does not match public URL")
    if not isinstance(observations, list) or len(observations) < 2:
        raise ValueError("transition evidence needs at least two observations")
    parsed = []
    for observation in observations:
        if observation.get("externally_observed") is not True:
            raise ValueError("every transition observation must be externally_observed=true")
        target = observation.get("target")
        if target not in {"home", "oracle"}:
            raise ValueError("transition target must be home or oracle")
        timestamp = datetime.fromisoformat(
            str(observation["observed_at"]).replace("Z", "+00:00")
        ).timestamp()
        parsed.append((timestamp, target))
    parsed.sort()
    targets = {target for _, target in parsed}
    if targets != {"home", "oracle"}:
        raise ValueError("transition evidence must observe both home and oracle")
    return {
        "status": "verified",
        "observations": len(parsed),
        "first_target": parsed[0][1],
        "last_target": parsed[-1][1],
    }


def run_proof(
    *,
    home_origin: str,
    oracle_origin: str,
    public_url: str,
    opener: Callable[..., Any] = urllib.request.urlopen,
    now: Callable[[], float] = time.time,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    detection_assumption_seconds: int = DEFAULT_DETECTION_ASSUMPTION_SECONDS,
    convergence_assumption_seconds: int = DEFAULT_CONVERGENCE_ASSUMPTION_SECONDS,
    public_origin_header: str | None = None,
    expected_public_origin: str | None = None,
    transition_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started = now()
    origins = {
        "home": _probe_site(
            "home", home_origin, opener=opener, now=now, timeout=timeout,
            capture_header=public_origin_header,
        ),
        "oracle": _probe_site(
            "oracle", oracle_origin, opener=opener, now=now, timeout=timeout,
            capture_header=public_origin_header,
        ),
    }
    public_route = _public_route(
        public_url,
        opener=opener,
        now=now,
        timeout=timeout,
        public_origin_header=public_origin_header,
        expected_public_origin=expected_public_origin,
    )
    public_hostname = urllib.parse.urlsplit(public_url).hostname or public_url
    if transition_evidence is None:
        cloudflare_failover = {
            "status": "not_verified",
            "reason": "no external route transition was observed",
        }
    else:
        cloudflare_failover = validate_transition_evidence(
            transition_evidence, public_hostname=public_hostname
        )
    finished = now()
    return {
        "schema": "pantrybot-public-routing-proof/v1",
        "generated_at": utc_timestamp(finished),
        "started_at": utc_timestamp(started),
        "finished_at": utc_timestamp(finished),
        "elapsed_ms": round(max(0.0, finished - started) * 1000, 1),
        "origins": origins,
        "public_route": public_route,
        "cloudflare_failover": cloudflare_failover,
        "assumptions": {
            "detection_seconds": detection_assumption_seconds,
            "route_convergence_seconds": convergence_assumption_seconds,
            "measured": False,
            "note": "Assumptions are recorded for rehearsal planning; this run does not mutate or measure route convergence.",
        },
        "ok": all(site["ok"] for site in origins.values()) and public_route["ok"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home-origin", required=True)
    parser.add_argument("--oracle-origin", required=True)
    parser.add_argument("--public-url", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--detection-assumption-seconds", type=int, default=DEFAULT_DETECTION_ASSUMPTION_SECONDS)
    parser.add_argument("--convergence-assumption-seconds", type=int, default=DEFAULT_CONVERGENCE_ASSUMPTION_SECONDS)
    parser.add_argument("--public-origin-header")
    parser.add_argument("--expected-public-origin", choices=("home", "oracle"))
    parser.add_argument(
        "--transition-evidence",
        type=Path,
        help="JSON artifact with externally observed before/after route targets",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if bool(args.public_origin_header) != bool(args.expected_public_origin):
        raise SystemExit("--public-origin-header and --expected-public-origin must be supplied together")
    transition_evidence = (
        json.loads(args.transition_evidence.read_text())
        if args.transition_evidence
        else None
    )
    evidence = run_proof(
        home_origin=args.home_origin,
        oracle_origin=args.oracle_origin,
        public_url=args.public_url,
        timeout=args.timeout,
        detection_assumption_seconds=args.detection_assumption_seconds,
        convergence_assumption_seconds=args.convergence_assumption_seconds,
        public_origin_header=args.public_origin_header,
        expected_public_origin=args.expected_public_origin,
        transition_evidence=transition_evidence,
    )
    encoded = json.dumps(evidence, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    print(encoded, end="")
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
