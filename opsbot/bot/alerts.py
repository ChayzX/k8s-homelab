"""Grafana alert relay: `POST /alerts/grafana` -> Discord DM to the owner.

Grafana's `webhook` contact point posts its notification JSON here and opsbot
turns each notification into one short DM to DISCORD_USER_ID, using the same
bot identity and DM path the rest of opsbot already has. There is no Discord
webhook anywhere in this path.

Contract (every non-2xx makes Grafana retry the notification later):

* 401 -- missing or wrong `Authorization: Bearer <OPSBOT_ALERT_TOKEN>`.
         Compared with hmac.compare_digest (constant time). Checked BEFORE the
         body is read, so unauthenticated callers cannot make us parse JSON.
* 413 -- body larger than MAX_BODY_BYTES (64 KiB).
* 400 -- body is not a JSON object with an `alerts` list.
* 503 -- this pod cannot deliver right now: OPSBOT_ALERT_TOKEN unset, the
         site lease is not held (Oracle standby / fenced), Discord is not
         connected, or the DM send failed/timed out. Never a silent drop.
* 200 -- DM sent.
* 202 -- accepted but coalesced by the rate limiter: more than
         MAX_DMS_PER_WINDOW notifications arrived inside WINDOW_SECONDS. The
         excess is counted and summarised in ONE follow-up DM once the window
         has room again (a failed summary is retried, not discarded). The
         summary lives in memory only, so a pod restart before it is flushed
         loses it; accepted, because every coalesced notification was already
         logged and Grafana re-notifies still-firing alerts on repeat_interval.

Logging: one line per request with status/alertname/count/result. Never the
Authorization header, the token, or the raw payload.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import time
from collections import Counter, deque
from typing import Awaitable, Callable

from aiohttp import web

MAX_BODY_BYTES = 64 * 1024
MAX_DMS_PER_WINDOW = 5
WINDOW_SECONDS = 60.0
SEND_TIMEOUT_SECONDS = 15.0
DISCORD_LIMIT = 1900  # Discord hard limit is 2000; keep headroom.
MAX_ALERT_LINES = 5
# Labels shown in the header or carrying no information for a phone DM.
_HIDDEN_LABELS = {"alertname", "grafana_folder", "__alert_rule_uid__"}

Sender = Callable[[str], Awaitable[None]]


class DeliveryError(Exception):
    """The DM could not be delivered; the caller answers 503."""


def _log(message: str) -> None:
    print(f"[opsbot] alert relay: {message}")


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _alertname(payload: dict) -> str:
    for source in (payload.get("groupLabels"), payload.get("commonLabels")):
        if isinstance(source, dict) and source.get("alertname"):
            return str(source["alertname"])
    names = {
        str(alert.get("labels", {}).get("alertname"))
        for alert in payload.get("alerts", [])
        if isinstance(alert, dict) and isinstance(alert.get("labels"), dict)
        and alert["labels"].get("alertname")
    }
    if len(names) == 1:
        return names.pop()
    return f"{len(names)} alert rules" if names else "unknown alert"


def summarize(payload: dict) -> tuple[str, str, int]:
    """(STATUS, alertname, alert count) -- also used for log lines."""
    alerts = [a for a in payload.get("alerts", []) if isinstance(a, dict)]
    status = str(payload.get("status") or "unknown").upper()
    return status, _alertname(payload), len(alerts)


def format_notification(payload: dict) -> str:
    """One concise, phone-readable DM for a Grafana webhook notification."""
    status, alertname, count = summarize(payload)
    alerts = [a for a in payload.get("alerts", []) if isinstance(a, dict)]
    firing = sum(1 for a in alerts if a.get("status") == "firing")
    resolved = sum(1 for a in alerts if a.get("status") == "resolved")
    common = payload.get("commonLabels") if isinstance(payload.get("commonLabels"), dict) else {}

    header = f"**[{status}] {_clip(alertname, 120)}**"
    counts = f"{count} alert{'s' if count != 1 else ''}"
    if firing and resolved:
        counts += f" ({firing} firing, {resolved} resolved)"
    lines = [header, counts]

    context = [
        f"{key}={_clip(common[key], 60)}"
        for key in ("grafana_folder", "severity")
        if common.get(key)
    ]
    if context:
        lines.append(" · ".join(context))

    for alert in alerts[:MAX_ALERT_LINES]:
        labels = alert.get("labels") if isinstance(alert.get("labels"), dict) else {}
        annotations = alert.get("annotations") if isinstance(alert.get("annotations"), dict) else {}
        distinct = [
            f"{key}={_clip(value, 60)}"
            for key, value in sorted(labels.items())
            if key not in _HIDDEN_LABELS and key not in common
        ][:4]
        text = annotations.get("summary") or annotations.get("description") or ""
        marker = "resolved" if alert.get("status") == "resolved" else "firing"
        detail = " ".join(part for part in (" ".join(distinct), _clip(text, 200)) if part)
        lines.append(f"• ({marker}) {detail}" if detail else f"• ({marker})")
    if len(alerts) > MAX_ALERT_LINES:
        lines.append(f"• …and {len(alerts) - MAX_ALERT_LINES} more")

    link = ""
    for alert in alerts:
        link = alert.get("generatorURL") or ""
        if link:
            break
    link = link or payload.get("externalURL") or ""
    if isinstance(link, str) and link.startswith(("http://", "https://")):
        lines.append(f"<{_clip(link, 300)}>")

    text = "\n".join(lines)
    return text if len(text) <= DISCORD_LIMIT else text[: DISCORD_LIMIT - 1] + "…"


class AlertRelay:
    """Authenticates, rate-limits and forwards Grafana notifications."""

    def __init__(
        self,
        token: str | None,
        send: Sender,
        can_deliver: Callable[[], bool],
        *,
        max_per_window: int = MAX_DMS_PER_WINDOW,
        window_seconds: float = WINDOW_SECONDS,
        send_timeout: float = SEND_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        flush_poll_seconds: float = 1.0,
    ) -> None:
        self._token = (token or "").strip().encode()
        self._send = send
        self._can_deliver = can_deliver
        self._max = max_per_window
        self._window = window_seconds
        self._send_timeout = send_timeout
        self._clock = clock
        self._flush_poll = flush_poll_seconds
        self._sent: deque[float] = deque()
        self._suppressed: Counter[tuple[str, str]] = Counter()
        self._lock = asyncio.Lock()
        self._flush_task: asyncio.Task | None = None

    @classmethod
    def from_env(cls, send: Sender, can_deliver: Callable[[], bool]) -> "AlertRelay":
        relay = cls(os.environ.get("OPSBOT_ALERT_TOKEN"), send, can_deliver)
        if not relay._token:
            _log("WARNING: OPSBOT_ALERT_TOKEN is not set -- every alert will get 503")
        return relay

    # -- auth / parsing -------------------------------------------------
    def _authorized(self, header: str | None) -> bool:
        if not self._token or not header:
            return False
        scheme, _, provided = header.partition(" ")
        if scheme.lower() != "bearer":
            return False
        return hmac.compare_digest(provided.strip().encode(), self._token)

    # -- rate limiting --------------------------------------------------
    def _prune(self) -> None:
        cutoff = self._clock() - self._window
        while self._sent and self._sent[0] <= cutoff:
            self._sent.popleft()

    def _has_budget(self) -> bool:
        self._prune()
        return len(self._sent) < self._max

    def _suppressed_summary(self) -> str:
        total = sum(self._suppressed.values())
        parts = [
            f"{status} {name} x{n}"
            for (status, name), n in self._suppressed.most_common(8)
        ]
        more = len(self._suppressed) - len(parts)
        tail = f", +{more} more" if more > 0 else ""
        return (
            f"**[COALESCED]** {total} more Grafana notification"
            f"{'s' if total != 1 else ''} arrived while rate-limited "
            f"(max {self._max}/{int(self._window)}s): {', '.join(parts)}{tail}"
        )[:DISCORD_LIMIT]

    async def _deliver(self, text: str) -> None:
        if not self._can_deliver():
            raise DeliveryError("not the Discord owner or Discord not connected")
        try:
            await asyncio.wait_for(self._send(text), timeout=self._send_timeout)
        except asyncio.TimeoutError as error:
            raise DeliveryError("Discord DM timed out") from error
        except DeliveryError:
            raise
        except Exception as error:  # discord.HTTPException, network errors
            raise DeliveryError(f"Discord DM failed: {type(error).__name__}") from error
        self._sent.append(self._clock())

    def _schedule_flush(self) -> None:
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.get_running_loop().create_task(self._flush_loop())

    async def _flush_loop(self) -> None:
        """Send one summary DM once the window has room; retry on failure."""
        retry = 0.0
        not_before = 0.0
        while self._suppressed:
            await asyncio.sleep(self._flush_poll)
            if self._clock() < not_before:
                continue
            async with self._lock:
                if not self._suppressed:
                    return
                if not self._has_budget():
                    continue
                text = self._suppressed_summary()
                count = sum(self._suppressed.values())
                try:
                    await self._deliver(text)
                except DeliveryError as error:
                    retry = min(retry * 2 or 5.0, 60.0)
                    not_before = self._clock() + retry
                    _log(f"coalesced summary not sent ({error}); retrying in {retry:.0f}s")
                    continue
                self._suppressed.clear()
                _log(f"coalesced summary sent covering {count} notifications")

    # -- HTTP -----------------------------------------------------------
    async def handle(self, request: web.Request) -> web.Response:
        if not self._token:
            _log("rejected: OPSBOT_ALERT_TOKEN not configured (503)")
            return web.json_response({"error": "alert relay not configured"}, status=503)
        if not self._authorized(request.headers.get("Authorization")):
            _log("rejected: missing or invalid bearer token (401)")
            return web.json_response(
                {"error": "unauthorized"},
                status=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        if request.content_length is not None and request.content_length > MAX_BODY_BYTES:
            _log(f"rejected: body {request.content_length} bytes > {MAX_BODY_BYTES} (413)")
            return web.json_response({"error": "payload too large"}, status=413)
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = await request.content.read(16 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_BODY_BYTES:
                _log(f"rejected: body > {MAX_BODY_BYTES} bytes (413)")
                return web.json_response({"error": "payload too large"}, status=413)
            chunks.append(chunk)
        try:
            payload = json.loads(b"".join(chunks))
        except (ValueError, UnicodeDecodeError):
            _log("rejected: body is not valid JSON (400)")
            return web.json_response({"error": "invalid JSON"}, status=400)
        if not isinstance(payload, dict) or not isinstance(payload.get("alerts"), list):
            _log("rejected: JSON is not a Grafana notification (400)")
            return web.json_response({"error": "expected Grafana webhook JSON"}, status=400)

        status, alertname, count = summarize(payload)
        alertname = _clip(alertname, 100)
        what = f"status={status} alertname={alertname!r} alerts={count}"
        async with self._lock:
            if not self._can_deliver():
                _log(f"{what} result=503 (not owner / Discord not connected)")
                return web.json_response(
                    {"error": "opsbot cannot deliver to Discord from this pod"}, status=503
                )
            if not self._has_budget():
                self._suppressed[(status, alertname)] += 1
                self._schedule_flush()
                _log(f"{what} result=coalesced (rate limit)")
                return web.json_response({"status": "coalesced"}, status=202)
            try:
                await self._deliver(format_notification(payload))
            except DeliveryError as error:
                _log(f"{what} result=503 ({error})")
                return web.json_response({"error": "Discord delivery failed"}, status=503)
        _log(f"{what} result=sent")
        return web.json_response({"status": "sent"})
