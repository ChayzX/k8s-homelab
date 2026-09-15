# Canada host always-on runtime

Canada runs the PantryBot recovery workload with Docker Desktop. The Docker
Desktop Windows service must be configured for automatic startup so the
engine is available after a reboot before any user signs in:

```powershell
sc.exe config com.docker.service start= auto
```

The service runs as `LocalSystem`; application containers remain managed by
the Canada authority supervisor. Do not stop or recreate the containers while
applying this setting. Verify with:

```powershell
sc.exe qc com.docker.service
docker ps
```

Expected state is `START_TYPE : 2 AUTO_START` and all eight PantryBot
containers running. The legacy Podman machine task is not part of the active
runtime because this host uses Docker Desktop.
