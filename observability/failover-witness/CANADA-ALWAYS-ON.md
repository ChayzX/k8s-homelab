# Canada host always-on runtime

Canada runs the PantryBot recovery workload with Docker Desktop.

**Correction (2026-09-21, found live):** setting the `com.docker.service`
Windows service to auto-start is necessary but **not sufficient**. Docker
Desktop's actual container engine only comes up once the Docker Desktop GUI
application itself initializes its WSL2 backend (`wsl -l -v` shows the
`docker-desktop` distro as `Running`) — that requires an interactive
session, not just the background service. Confirmed live: the service can
show `RUNNING` for a long time while `docker ps` still fails and the
`docker-desktop` WSL distro sits `Stopped`, with multiple stale
`Docker Desktop.exe`/`com.docker.backend.exe` processes stuck mid-restart. A
clean recovery is: kill every `Docker Desktop.exe`/`com.docker.backend.exe`
process, `wsl --shutdown`, then trigger the existing `PantryBot Docker
Desktop` scheduled task (`schtasks /run /tn "PantryBot Docker Desktop"`) —
that task's "Interactive/Background" logon mode is what actually gets it
into the interactive session's context, unlike launching the exe directly
over an SSH session (which runs in the non-interactive service session and
does not fully initialize Docker Desktop).

The service still runs as `LocalSystem` and should stay auto-start:

```powershell
sc.exe config com.docker.service start= auto
```

Application containers remain managed by the Canada authority supervisor. Do
not stop or recreate the containers while applying this setting. Verify with:

```powershell
sc.exe qc com.docker.service
wsl -l -v
docker ps
```

Expected steady state: `START_TYPE : 2 AUTO_START`, `docker-desktop` WSL
distro `Running`, and the two safe-stage containers (`public-site`,
`private-site`) running — **not** all eight; the other six are gated behind
the `production` Compose profile and only run during an actual controlled
cutover, per `docker-compose.canada.yml`. This doc previously claimed "all
eight containers running" as the expected steady state, which described a
live cutover, not normal safe-stage operation.

**Known remaining gap, not fixed here:** whether the `PantryBot Docker
Desktop` scheduled task actually fires successfully on a *cold boot* with no
one logged into the console depends on whether BotAdmin auto-logs-in at
startup, which this doc does not configure and which was not verified live.
Setting up Windows auto-logon means storing a plaintext credential in the
registry — a real security trade-off — so it's flagged here for a decision
rather than silently configured. The legacy Podman machine task is not part
of the active runtime because this host uses Docker Desktop.
