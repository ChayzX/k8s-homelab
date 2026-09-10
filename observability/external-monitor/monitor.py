#!/usr/bin/env python3
"""Independent public-service monitor for a home-hosted cluster.

The monitor is intentionally dependency-free so it can run on a tiny external
VM. It writes a JSON state file, logs every transition, and optionally sends a
Discord webhook alert. It does not use Kubernetes credentials or Authentik.
"""

from __future__ import annotations

import json
import hashlib
import hmac
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from xml.etree import ElementTree
from pathlib import Path


INTERVAL = int(os.environ.get("MONITOR_INTERVAL_SECONDS", "60"))
TIMEOUT = int(os.environ.get("MONITOR_TIMEOUT_SECONDS", "12"))
FAILURE_THRESHOLD = int(os.environ.get("MONITOR_FAILURE_THRESHOLD", "3"))
STATE_FILE = Path(os.environ.get("MONITOR_STATE_FILE", "/var/lib/homelab-monitor/state.json"))
WEBHOOK = os.environ.get("MONITOR_DISCORD_WEBHOOK", "")
API_URL = os.environ.get("MONITOR_API_URL", "")
API_EXPECTED_STATUS = os.environ.get("MONITOR_API_EXPECTED_STATUS", "200")
R2_BUCKET = os.environ.get("MONITOR_R2_BUCKET", "")
R2_ENDPOINT = os.environ.get("MONITOR_R2_ENDPOINT", "").rstrip("/")
R2_ACCESS_KEY_ID = os.environ.get("MONITOR_R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.environ.get("MONITOR_R2_SECRET_ACCESS_KEY", "")
R2_PREFIXES = os.environ.get("MONITOR_R2_PREFIXES", "")


@dataclass(frozen=True)
class Check:
    name: str
    url: str
    expected: frozenset[int]


CHECKS = (
    Check("status", "https://status.greeniespantry.uk/", frozenset({200, 301, 302, 404})),
    Check("grafana", "https://grafana.greeniespantry.uk/", frozenset({200, 301, 302})),
    Check("oauth", "https://oauth.greeniespantry.uk/", frozenset({200, 301, 302, 404})),
    Check("authentik", "https://auth.greeniespantry.uk/", frozenset({200, 301, 302})),
)


def parse_expected_statuses(value: str) -> frozenset[int]:
    """Parse a comma-separated HTTP status allowlist."""
    if not value.strip():
        return frozenset({200})
    return frozenset(int(item.strip()) for item in value.split(",") if item.strip())


def check(item: Check) -> dict[str, object]:
    request = urllib.request.Request(item.url, headers={"User-Agent": "homelab-external-monitor/1"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            status = response.status
        return {
            "ok": status in item.expected,
            "status": status,
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
        }
    except urllib.error.HTTPError as exc:
        return {
            "ok": exc.code in item.expected,
            "status": exc.code,
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
        }
    except Exception as exc:  # noqa: BLE001 - monitor must survive network errors
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
        }


def parse_timestamp(value: str) -> float:
    """Parse an S3 LastModified timestamp into epoch seconds."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def r2_freshness_from_xml(
    document: bytes,
    *,
    prefix: str,
    max_age_seconds: int,
    now: float | None = None,
) -> dict[str, object]:
    """Evaluate the newest object under an S3 ListObjectsV2 response."""
    now = time.time() if now is None else now
    newest: tuple[float, str, int] | None = None
    root = ElementTree.fromstring(document)
    for item in root.iter():
        if item.tag.rsplit("}", 1)[-1] != "Contents":
            continue
        fields = {child.tag.rsplit("}", 1)[-1]: child.text for child in item}
        key = fields.get("Key") or ""
        if not key.startswith(prefix):
            continue
        modified = fields.get("LastModified")
        if not modified:
            continue
        timestamp = parse_timestamp(modified)
        size = int(fields.get("Size") or "0")
        candidate = (timestamp, key, size)
        if newest is None or candidate[0] > newest[0]:
            newest = candidate
    if newest is None:
        return {"ok": False, "reason": "missing"}
    age = max(0, round(now - newest[0], 1))
    result = {
        "ok": age <= max_age_seconds,
        "key": newest[1],
        "size": newest[2],
        "age_seconds": age,
        "max_age_seconds": max_age_seconds,
    }
    if not result["ok"]:
        result["reason"] = "stale"
    return result


def _sign(key: bytes, value: str) -> bytes:
    return hmac.new(key, value.encode(), hashlib.sha256).digest()


def _r2_request(prefix: str) -> bytes:
    """List one R2 prefix using AWS Signature Version 4 and stdlib only."""
    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date = now.strftime("%Y%m%d")
    endpoint = urllib.parse.urlsplit(R2_ENDPOINT)
    host = endpoint.netloc
    path = f"{endpoint.path.rstrip('/')}/{urllib.parse.quote(R2_BUCKET, safe='')}"
    query_items = [("list-type", "2"), ("prefix", prefix)]
    canonical_query = urllib.parse.urlencode(
        sorted(query_items), quote_via=urllib.parse.quote, safe=""
    )
    payload_hash = hashlib.sha256(b"").hexdigest()
    canonical_headers = (
        f"host:{host}\n"
        f"x-amz-content-sha256:{payload_hash}\n"
        f"x-amz-date:{amz_date}\n"
    )
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(
        ("GET", path, canonical_query, canonical_headers, signed_headers, payload_hash)
    )
    scope = f"{date}/auto/s3/aws4_request"
    string_to_sign = "\n".join(
        (
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        )
    )
    date_key = _sign(("AWS4" + R2_SECRET_ACCESS_KEY).encode(), date)
    region_key = _sign(date_key, "auto")
    service_key = _sign(region_key, "s3")
    signing_key = _sign(service_key, "aws4_request")
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    url = f"{R2_ENDPOINT}{path}?{canonical_query}"
    request = urllib.request.Request(
        url,
        headers={
            "Host": host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
            "Authorization": (
                f"AWS4-HMAC-SHA256 Credential={R2_ACCESS_KEY_ID}/{scope}, "
                f"SignedHeaders={signed_headers}, Signature={signature}"
            ),
        },
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read()


def r2_check() -> dict[str, object]:
    """Check configured R2 prefixes; return a non-secret result structure."""
    if not R2_PREFIXES:
        return {"ok": True, "skipped": True}
    if not all((R2_BUCKET, R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY)):
        return {"ok": False, "error": "incomplete R2 monitor configuration"}
    results: dict[str, object] = {}
    try:
        for entry in R2_PREFIXES.split(","):
            name, value = entry.split("=", 1)
            prefix, max_age = value.rsplit(":", 1)
            results[name] = r2_freshness_from_xml(
                _r2_request(prefix),
                prefix=prefix,
                max_age_seconds=int(max_age),
            )
        return {"ok": all(result["ok"] for result in results.values()), "prefixes": results}
    except Exception as exc:  # noqa: BLE001 - monitor must survive provider errors
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
def load_state() -> dict[str, object]:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def notify(message: str) -> None:
    if not WEBHOOK:
        print(f"ALERT {message}", flush=True)
        return
    payload = json.dumps({"content": f"**Homelab external monitor**\n{message}"}).encode()
    request = urllib.request.Request(
        WEBHOOK,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "homelab-external-monitor/1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT):
            pass
    except Exception as exc:  # noqa: BLE001 - report notification failure locally
        print(f"NOTIFICATION_FAILURE {type(exc).__name__}: {exc}", flush=True)


def run_once() -> None:
    checks = {item.name: check(item) for item in CHECKS}
    if API_URL:
        checks["kubernetes-api"] = check(
            Check("kubernetes-api", API_URL, parse_expected_statuses(API_EXPECTED_STATUS))
        )
    if R2_PREFIXES:
        checks["r2"] = r2_check()
    failed = sorted(name for name, result in checks.items() if not result["ok"])
    previous = load_state()
    failure_count = (int(previous.get("failure_count", 0)) + 1) if failed else 0
    overall = "degraded" if failure_count >= FAILURE_THRESHOLD else "healthy"
    state = {
        "timestamp": int(time.time()),
        "overall": overall,
        "failure_count": failure_count,
        "checks": checks,
    }
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")
    if previous.get("overall") and previous.get("overall") != overall:
        notify(f"State changed: `{previous['overall']}` → `{overall}`; failed checks: {', '.join(failed) or 'none'}")
    if failed:
        print(f"CHECK_FAILURE count={failure_count}/{FAILURE_THRESHOLD} failed={','.join(failed)}", flush=True)
    else:
        print("HEALTHY", flush=True)


if __name__ == "__main__":
    while True:
        run_once()
        time.sleep(INTERVAL)
