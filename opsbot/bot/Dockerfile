# syntax=docker/dockerfile:1
#
# opsbot -- Discord bot, Python. Locally-built-only, no registry: sideload
# exactly like ../../minecraft/Dockerfile does, per ../40-deployment.yaml's
# header comment.
#
#   docker build -t localhost/opsbot:dev .
#   docker save localhost/opsbot:dev | sudo k3s ctr images import -
#
# python:3.12-slim-bookworm (glibc, not Alpine/musl) so discord.py's and the
# kubernetes client's dependencies install from prebuilt wheels -- no
# compiler toolchain needed in the image at all.
FROM python:3.12-slim-bookworm

# `bd` (beads CLI) -- pinned to the exact version running on the host so the
# on-disk Dolt DB schema matches (a pod-side bd of a different minor could
# trip schema-skew guards when /report writes into the mounted boards). The
# runtime stage only needs the binary; this stage holds the download tooling.
# /report (bd_ops.py) invokes this from the container; no git is required.
FROM debian:bookworm-slim AS beads
ARG BEADS_VERSION=1.1.2
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && curl -fsSL -o /tmp/beads.tar.gz \
      "https://github.com/gastownhall/beads/releases/download/v${BEADS_VERSION}/beads_${BEADS_VERSION}_linux_amd64.tar.gz" \
 && tar -xzf /tmp/beads.tar.gz -C /usr/local/bin bd \
 && chmod +x /usr/local/bin/bd \
 && rm -f /tmp/beads.tar.gz

FROM python:3.12-slim-bookworm

# Unbuffered stdout -- without this, print() output (including the audit
# log lines bi6.5 requires) sits in a buffer instead of reaching `kubectl
# logs` immediately. Confirmed live: the bot connected to Discord's gateway
# but its own startup/audit print() lines didn't appear for 20+ seconds
# after connection -- classic Python-in-a-container buffering, not a hang.
# A crash before the buffer flushes would silently lose audit entries.
ENV PYTHONUNBUFFERED=1

# bd writes per-user state (e.g. the beads.role warning) under HOME; /app is
# owned by uid 1000 below, so point HOME there rather than at /root.
ENV HOME=/app

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --from=beads /usr/local/bin/bd /usr/local/bin/bd

COPY main.py bd_ops.py k8s_ops.py util.py ./

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
