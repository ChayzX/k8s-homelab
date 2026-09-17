"""HTTP health endpoint for Kuma monitoring -- same pattern as jmusicbot's
same reasoning as jmusicbot: opsbot
has no listener at all
(Discord-only, outbound gateway connection), so there's no network-level way
to check it's actually working. aiohttp is already a hard dependency of
discord.py, so this adds no new pip package.

/live   -> 200 once the HTTP server itself is bound. Liveness: process is
           alive. A transient Discord outage should not restart the pod.
/health -> 200 once logged into Discord (on_ready fired), else 503
           "starting". Readiness: don't route/consider-up until actually
           connected. (alias: /healthz)
"""
from __future__ import annotations

import asyncio
import threading

from aiohttp import web

_ready = False


def mark_ready() -> None:
    global _ready
    _ready = True


def mark_not_ready() -> None:
    """Withdraw readiness immediately when ownership or runtime is fenced."""
    global _ready
    _ready = False


async def _live(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def _health(_request: web.Request) -> web.Response:
    if _ready:
        return web.json_response({"status": "ok"})
    return web.json_response({"status": "starting"}, status=503)


def _application() -> web.Application:
    app = web.Application()
    app.add_routes(
        [
            web.get("/live", _live),
            web.get("/health", _health),
            web.get("/healthz", _health),
        ]
    )
    return app


async def start(port: int = 9091) -> None:
    app = _application()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"[opsbot] health endpoint listening on :{port}")


def start_background(port: int = 9091):
    """Serve liveness/readiness while a standby waits for ownership.

    The returned callback stops the private event loop before the Discord bot
    starts its normal async health server. Standby readiness remains 503 until
    Discord's on_ready callback marks it ready.
    """
    loop = asyncio.new_event_loop()
    started = threading.Event()
    failure: list[BaseException] = []
    runner_holder: list[web.AppRunner] = []

    async def launch() -> None:
        app = _application()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        print(f"[opsbot] standby health endpoint listening on :{port}")
        return runner

    def run() -> None:
        asyncio.set_event_loop(loop)
        try:
            runner_holder.append(loop.run_until_complete(launch()))
            started.set()
            loop.run_forever()
        except BaseException as error:  # pragma: no cover - background path
            failure.append(error)
            started.set()
        finally:
            loop.close()

    thread = threading.Thread(target=run, name="opsbot-health-standby", daemon=True)
    thread.start()
    if not started.wait(timeout=5):
        raise RuntimeError("standby health endpoint did not start")
    if failure:
        raise RuntimeError("standby health endpoint failed") from failure[0]

    def stop() -> None:
        if loop.is_running():
            async def cleanup() -> None:
                if runner_holder:
                    await runner_holder[0].cleanup()

            asyncio.run_coroutine_threadsafe(cleanup(), loop).result(timeout=5)
            loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)

    return stop
