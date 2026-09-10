# syntax=docker/dockerfile:1
#
# opsbot -- Discord bot, Python. Built + pushed to GHCR by
# .github/workflows/opsbot-deploy.yml on every push to opsbot/bot/**, then
# deployed by the same workflow. No more local sideload -- see
# ../40-deployment.yaml's header comment (k8s-homelab-oiv).
#
# python:3.12-slim-bookworm (glibc, not Alpine/musl) so discord.py's and the
# kubernetes client's dependencies install from prebuilt wheels -- no
# compiler toolchain needed in the image at all.
FROM python:3.12-slim-bookworm

# Unbuffered stdout -- without this, print() output (including the audit
# log lines bi6.5 requires) sits in a buffer instead of reaching `kubectl
# logs` immediately. Confirmed live: the bot connected to Discord's gateway
# but its own startup/audit print() lines didn't appear for 20+ seconds
# after connection -- classic Python-in-a-container buffering, not a hang.
# A crash before the buffer flushes would silently lose audit entries.
ENV PYTHONUNBUFFERED=1

# Writable per-user state lives under /app (uid 1000 owns it, below).
ENV HOME=/app

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py gh_ops.py health.py k8s_ops.py ownership.py util.py _operations_contract.py ./

# uid/gid 1000 to match ../40-deployment.yaml's securityContext
# (runAsNonRoot, runAsUser: 1000, runAsGroup: 1000) -- python:*-slim has no
# uid 1000 user by default the way the Ubuntu-based minecraft image does, so
# it's created explicitly here.
RUN groupadd --gid 1000 opsbot \
 && useradd --uid 1000 --gid 1000 --home-dir /app --no-create-home --shell /usr/sbin/nologin opsbot \
 && chown -R 1000:1000 /app

USER 1000:1000

# No listening socket, no HTTP endpoint -- this is a Discord gateway client
# (outbound only), same reasoning as ../40-deployment.yaml's "no probes"
# comment. Nothing to EXPOSE.

ENTRYPOINT ["python3", "main.py"]
