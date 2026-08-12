#!/usr/bin/env bash
# Keeps jmusicbot updated while we're stuck on a locally-built image.
# k3s port of /home/chase/docker/jmusicbot/auto-update.sh -- same logic,
# different deploy step. Read the "What changed vs the Compose version"
# section below before touching anything.
#
# Background: arif-banai/MusicBot bundles youtube-source 1.18.1, which has a
# bug (itag 18 content-length misdetection) that breaks playback of some
# videos even with a valid YouTube OAuth token. Fixed upstream in
# youtube-source 1.18.2. arif-banai/MusicBot hadn't picked up the bump as of
# 2026-08-09, so we're running a locally-built image (custom-build/) with the
# version bumped in pom.xml, and auto-updates disabled on this workload since
# nothing can auto-update a local-only tag. See JMUSICBOT_YOUTUBE_HANDOFF.md.
#
# This script restores "auto receive updates" while that patch is in place:
#   1. If arif-banai/MusicBot's latest release now bundles youtube-source
#      >= FIX_VERSION itself, the patch is no longer needed: switch back to
#      the stock ghcr.io image, hand the workload over to Keel for ongoing
#      auto-updates, and uninstall the cron job that runs this script
#      (one-time, self-terminating transition).
#   2. Otherwise, if there's a newer MusicBot release and/or a newer
#      youtube-source release than what we last built, rebuild the custom
#      image from the latest MusicBot tag with the latest youtube-source
#      version patched in, and redeploy. This is intentionally close to what
#      an auto-updater would have done (auto-deploy new official tags,
#      unreviewed) -- just done via a source rebuild instead of an image
#      pull, since we need the patch applied on top.
#   3. If nothing changed since the last run, do nothing.
#
# Uses unauthenticated GitHub API calls (public repos, ~60 req/hr limit,
# plenty for a script that runs once a day) instead of `gh` -- `gh`'s token
# lives in the desktop keyring here and isn't reliably unlockable from cron.
#
# ---------------------------------------------------------------------------
# What changed vs the Compose version
# ---------------------------------------------------------------------------
# * The `docker build` STAYS. That is the whole reason native docker-ce is
#   kept installed on this host after the k3s migration. We are not standing
#   up a registry for a single-node homelab.
# * The built image is side-loaded into k3s's embedded containerd with
#   `docker save <tag> | sudo k3s ctr images import -`. k3s does not see
#   docker's image store; without this step the pod would sit in
#   ErrImagePull forever trying to fetch a tag that exists nowhere.
#   That import needs root -> see the sudoers requirement below.
# * Deploy is `kubectl set image`, NOT `kubectl rollout restart`. This is
#   load-bearing: every run builds a *uniquely tagged* image, so the pod
#   spec's image *reference* has to change for the rollout to pick up the
#   new build. `rollout restart` would faithfully redeploy the OLD tag and
#   report success, which is the worst possible failure mode (silent no-op
#   that looks like a successful update).
# * The manifest at $MANIFEST gets the same in-place image-line rewrite the
#   compose file used to get, so git stays the record of what's deployed and
#   a later `kubectl apply -f` doesn't silently roll the bot backwards.
# * Rollout is verified with `kubectl rollout status --timeout`; on failure
#   we `kubectl rollout undo`, restore the manifest, leave the state file
#   untouched (so the next run retries) and shout on Discord.
# * The "upstream fixed it" path no longer re-enables Watchtower -- there is
#   no Watchtower any more. See the KEEL HANDOVER note at Step 1.
#
# ---------------------------------------------------------------------------
# Requirements on the host (all one-time, all done by hand by the operator)
# ---------------------------------------------------------------------------
# 1. sudoers: this script runs from the *user* crontab, so `sudo` must not
#    prompt. Add a dedicated, scoped file -- NOT blanket NOPASSWD:ALL:
#
#      sudo visudo -f /etc/sudoers.d/jmusicbot-k3s-ctr
#      # contents (mode 0440, root:root):
#      chase ALL=(root) NOPASSWD: /usr/local/bin/k3s ctr images *
#
#    Scoped to `ctr images` specifically: that covers ls/import/rm (all this
#    script needs) without handing over `ctr run`/`ctr task`, which would be
#    trivially root-equivalent. Be honest about what this still grants: the
#    ability to plant an arbitrary image into containerd. That is a real
#    privilege, just a much smaller one than full sudo.
#    Note sudo resolves the command against secure_path, so the path in the
#    sudoers line must be the *absolute* path to the k3s binary; this script
#    checks that and refuses to guess.
#
# 2. The Deployment must not try to pull the locally-built tag. Unique
#    non-`:latest` tags already default to imagePullPolicy: IfNotPresent, so
#    the default is correct -- but if the manifest ever sets
#    `imagePullPolicy: Always`, every rollout will ErrImagePull. This script
#    warns loudly if it sees that.
#
# 3. KUBECONFIG. cron gets almost no environment; k3s is installed with
#    --write-kubeconfig-mode 644 so the default below just works.
#
# Run manually to test: ./auto-update.sh
# Installed via user crontab (see the bottom of this file for the line).

set -euo pipefail

# cron's PATH is typically /usr/bin:/bin -- k3s installs kubectl and k3s into
# /usr/local/bin, and docker lives in /usr/bin. Be explicit rather than
# inheriting whatever cron feels like providing.
export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"
export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"

# The build tree deliberately stays under ~/docker/jmusicbot: it is a *build
# context* plus this workload's .env (webhook URL) and the state file, none of
# which are Kubernetes objects. Keeping it put also means the .last-built-tag
# state survives the cutover, so the first post-cutover run doesn't do a
# pointless rebuild of an image we already have.
DIR="${JMUSICBOT_DIR:-/home/chase/docker/jmusicbot}"
BUILD_DIR="$DIR/custom-build"
STATE_FILE="$BUILD_DIR/.last-built-tag"

# The Kubernetes side.
MANIFEST="${JMUSICBOT_MANIFEST:-/home/chase/k8s-homelab/jmusicbot/40-deployment-jmusicbot.yaml}"
NAMESPACE="${JMUSICBOT_NAMESPACE:-jmusicbot}"
DEPLOYMENT="${JMUSICBOT_DEPLOYMENT:-jmusicbot}"
CONTAINER="${JMUSICBOT_CONTAINER:-jmusicbot}"   # container name inside the pod spec
ROLLOUT_TIMEOUT="${JMUSICBOT_ROLLOUT_TIMEOUT:-300s}"

# Commit the manifest edit locally (never push) so the repo is an accurate
# record of what's running. Only ever touches $MANIFEST's path, so unrelated
# working-tree changes are left alone. Set to 0 to disable.
AUTO_COMMIT_MANIFEST="${AUTO_COMMIT_MANIFEST:-1}"

FIX_VERSION="1.18.2"
STOCK_IMAGE="ghcr.io/arif-banai/musicbot:latest"
WEBHOOK_URL="$(grep -oP '(?<=DISCORD_RELEASE_WEBHOOK_URL=).*' "$DIR/.env" || true)"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

notify() {
    local msg="$1"
    log "NOTIFY: $msg"
    if [ -n "$WEBHOOK_URL" ]; then
        curl -sf -H "Content-Type: application/json" \
            -d "{\"content\": \"**jmusicbot auto-update:** ${msg}\"}" \
            "$WEBHOOK_URL" >/dev/null || log "WARN: Discord notification failed"
    fi
}

gh_api() {
    curl -sf -H "Accept: application/vnd.github+json" "https://api.github.com/$1"
}

die() {
    # Fatal + notify. Used for the environment problems that are worth waking
    # someone up for, because they mean the bot silently stops getting updates.
    log "ERROR: $*"
    notify "🔴 $*"
    exit 1
}

# --- Preflight: the k3s-specific things that can be wrong -------------------

require_tools() {
    local missing=()
    for t in kubectl docker jq curl git; do
        command -v "$t" >/dev/null 2>&1 || missing+=("$t")
    done
    [ ${#missing[@]} -eq 0 ] || die "missing required tools on PATH: ${missing[*]} (PATH=$PATH)"

    if ! kubectl get deployment "$DEPLOYMENT" -n "$NAMESPACE" >/dev/null 2>&1; then
        die "cannot reach deployment/${DEPLOYMENT} in namespace ${NAMESPACE} (KUBECONFIG=$KUBECONFIG). Is k3s up?"
    fi

    if [ ! -f "$MANIFEST" ]; then
        die "manifest not found at ${MANIFEST}; refusing to deploy an image that git has no record of."
    fi
}

# Verifies the scoped NOPASSWD sudoers entry actually works BEFORE we spend
# several minutes on a maven build we'd then be unable to deploy.
require_ctr_sudo() {
    K3S_BIN="$(command -v k3s || true)"
    [ -n "$K3S_BIN" ] || die "k3s binary not found on PATH (PATH=$PATH)"

    if [ "$K3S_BIN" != "/usr/local/bin/k3s" ]; then
        log "WARN: k3s resolved to ${K3S_BIN}, not /usr/local/bin/k3s -- the sudoers rule must name this exact path."
    fi

    if ! sudo -n "$K3S_BIN" ctr images ls -q >/dev/null 2>&1; then
        die "passwordless \`sudo k3s ctr images\` is not available, so the built image can't be imported into k3s containerd. Fix with: sudo visudo -f /etc/sudoers.d/jmusicbot-k3s-ctr  ->  'chase ALL=(root) NOPASSWD: ${K3S_BIN} ctr images *'  (mode 0440). See the header of auto-update.sh."
    fi
}

# Non-fatal, but the single most likely reason a rollout mysteriously fails.
warn_on_pull_policy() {
    local policy
    policy="$(kubectl get deployment "$DEPLOYMENT" -n "$NAMESPACE" \
        -o jsonpath="{.spec.template.spec.containers[?(@.name=='${CONTAINER}')].imagePullPolicy}" 2>/dev/null || true)"
    if [ "$policy" = "Always" ]; then
        log "WARN: container ${CONTAINER} has imagePullPolicy: Always. The locally-built tag exists only in containerd, so the kubelet will ErrImagePull. Remove it (unique tags default to IfNotPresent)."
    fi
}

# Confirms $CONTAINER actually exists in the pod spec -- `kubectl set image`
# with a wrong container name errors out, but better to say so in English.
require_container() {
    if ! kubectl get deployment "$DEPLOYMENT" -n "$NAMESPACE" \
        -o jsonpath="{.spec.template.spec.containers[*].name}" 2>/dev/null | tr ' ' '\n' | grep -qx "$CONTAINER"; then
        die "deployment/${DEPLOYMENT} has no container named '${CONTAINER}'. Set JMUSICBOT_CONTAINER to the right name."
    fi
}

# Commit just the manifest, locally, never push. Best-effort: a failure here
# is a bookkeeping problem, not a deployment problem.
commit_manifest() {
    local message="$1"
    [ "$AUTO_COMMIT_MANIFEST" = "1" ] || return 0
    local repo_dir
    repo_dir="$(cd "$(dirname "$MANIFEST")" && git rev-parse --show-toplevel 2>/dev/null || true)"
    [ -n "$repo_dir" ] || { log "WARN: ${MANIFEST} is not in a git repo; skipping commit."; return 0; }
    git -C "$repo_dir" add -- "$MANIFEST" 2>/dev/null || true
    if git -C "$repo_dir" diff --cached --quiet -- "$MANIFEST" 2>/dev/null; then
        return 0  # nothing staged, e.g. the file was already at this content
    fi
    git -C "$repo_dir" -c user.name="auto-update.sh" -c user.email="auto-update@localhost" \
        commit -q -m "$message" -- "$MANIFEST" \
        && log "Committed ${MANIFEST}: ${message}" \
        || log "WARN: git commit of ${MANIFEST} failed; commit it by hand."
}

require_tools

# --- Step 0: figure out where things stand upstream ---

latest_musicbot_tag="$(gh_api repos/arif-banai/MusicBot/releases/latest | jq -r .tag_name)"
if [ -z "$latest_musicbot_tag" ] || [ "$latest_musicbot_tag" = "null" ]; then
    log "ERROR: couldn't determine latest arif-banai/MusicBot release, aborting"
    exit 1
fi

upstream_pom="$(gh_api "repos/arif-banai/MusicBot/contents/pom.xml?ref=${latest_musicbot_tag}" | jq -r .content | base64 -d)"
upstream_yts_version="$(echo "$upstream_pom" | grep -oP '(?<=<youtube-source.version>)[^<]+')"

if [ -z "$upstream_yts_version" ]; then
    log "ERROR: couldn't parse youtube-source.version from upstream pom.xml at ${latest_musicbot_tag}, aborting"
    exit 1
fi

log "Latest MusicBot release: ${latest_musicbot_tag} (bundles youtube-source ${upstream_yts_version})"

# --- Step 1: has upstream fixed it themselves? ---
#
# KEEL HANDOVER (this replaces "re-enable Watchtower"):
# The Compose version flipped com.centurylinklabs.watchtower.enable back to
# true here, because Watchtower was the host-wide auto-updater and the only
# reason jmusicbot was opted out of it was the un-updatable local tag. That
# reason disappears the moment we're back on ghcr.io/arif-banai/musicbot:latest.
#
# In the k3s world the equivalent auto-updater is Keel, which is deliberately
# scoped to pantry-bot only -- for exactly the same reason jmusicbot was
# excluded from Watchtower: a local-only tag is not pollable. So the faithful
# port of "re-enable Watchtower" is "annotate the Deployment for Keel", which
# is what we do below: policy `force` + `poll` on a 5m schedule, matching
# pantry-bot's configuration, so `:latest` digest changes get rolled out the
# same way. This intentionally preserves the original's posture of
# unreviewed auto-deploys of upstream releases -- that is what the bot had
# before the patch and what the operator asked to get back.
#
# Keel needs to be able to poll ghcr.io for this image. It is a *public*
# package, unlike pantry-bot's, so no extra registry credentials are needed;
# if that ever changes, Keel needs a secret in its own namespace (separate
# concern from the pod's imagePullSecrets).
#
# Then we uninstall our own cron job: the transition is one-time.

# Inserts the Keel annotations into the Deployment's top-level metadata in
# $MANIFEST, preserving comments (which is why this is awk and not yq/kubectl
# patch --local -- both of those reformat the file and drop every comment).
# Idempotent: does nothing if keel.sh/policy is already present.
add_keel_annotations_to_manifest() {
    local file="$1"
    if grep -q 'keel\.sh/policy' "$file"; then
        log "Keel annotations already present in ${file}."
        return 0
    fi
    awk '
        BEGIN { in_deploy = 0; done = 0; pending_meta = 0 }
        # Track document boundaries so we only touch the Deployment doc.
        /^---[[:space:]]*$/ { in_deploy = 0 }
        /^kind:[[:space:]]*Deployment[[:space:]]*$/ { in_deploy = 1 }
        {
            # Case A: the Deployment already has a top-level annotations block
            # (2-space indent, i.e. directly under metadata:) -- append ours.
            if (in_deploy && !done && pending_meta && $0 ~ /^  annotations:[[:space:]]*$/) {
                print
                print "    # Handed over to Keel by auto-update.sh: upstream now ships the"
                print "    # youtube-source fix, so we are back on a pollable :latest tag."
                print "    keel.sh/policy: force"
                print "    keel.sh/trigger: poll"
                print "    keel.sh/pollSchedule: \"@every 5m\""
                done = 1; pending_meta = 0
                next
            }
            # Leaving metadata: without having seen annotations: -> insert a
            # fresh block. (Any other 0-indent-2 key after metadata: ends it.)
            if (in_deploy && !done && pending_meta && $0 ~ /^[a-zA-Z]/) {
                print "  annotations:"
                print "    # Handed over to Keel by auto-update.sh: upstream now ships the"
                print "    # youtube-source fix, so we are back on a pollable :latest tag."
                print "    keel.sh/policy: force"
                print "    keel.sh/trigger: poll"
                print "    keel.sh/pollSchedule: \"@every 5m\""
                done = 1; pending_meta = 0
            }
            if (in_deploy && !done && $0 ~ /^metadata:[[:space:]]*$/) {
                print
                pending_meta = 1
                next
            }
            print
        }
        END {
            if (pending_meta && !done) {
                print "  annotations:"
                print "    keel.sh/policy: force"
                print "    keel.sh/trigger: poll"
                print "    keel.sh/pollSchedule: \"@every 5m\""
            }
        }
    ' "$file" > "${file}.keel.tmp" && mv "${file}.keel.tmp" "$file"

    grep -q 'keel\.sh/policy' "$file"
}

if dpkg --compare-versions "$upstream_yts_version" ge "$FIX_VERSION"; then
    if grep -q "image: jmusicbot-custom:" "$MANIFEST"; then
        log "Upstream now bundles youtube-source ${upstream_yts_version} (>= ${FIX_VERSION}). Switching back to the stock image."

        require_container
        manifest_backup="$(mktemp)"
        cp "$MANIFEST" "$manifest_backup"

        # Same block rewrite the compose file used to get: replace the
        # custom-build explainer with a dated note about why it's gone.
        sed -i "/# BEGIN CUSTOM-BUILD-NOTE/,/# END CUSTOM-BUILD-NOTE/c\\
      # Switched back to the stock image by auto-update.sh on $(date '+%Y-%m-%d'):\\
      # arif-banai/MusicBot ${latest_musicbot_tag} now bundles youtube-source\\
      # ${upstream_yts_version} (>= ${FIX_VERSION}), which includes the itag-18\\
      # fix this used to need a custom build for. Auto-updates are now handled\\
      # by Keel (annotations above), the way pantry-bot's are.\\
      # See JMUSICBOT_YOUTUBE_HANDOFF.md." "$MANIFEST"
        sed -i "s|image: jmusicbot-custom:.*|image: ${STOCK_IMAGE}|" "$MANIFEST"

        keel_ok=1
        add_keel_annotations_to_manifest "$MANIFEST" || keel_ok=0

        # Validate against the live API before we trust our own text munging.
        if [ "$keel_ok" != "1" ] || ! kubectl apply --dry-run=server -f "$MANIFEST" >/dev/null 2>&1; then
            log "WARN: edited manifest failed validation; restoring it and falling back to live-object edits."
            cp "$manifest_backup" "$MANIFEST"
            kubectl set image "deployment/${DEPLOYMENT}" "${CONTAINER}=${STOCK_IMAGE}" -n "$NAMESPACE"
            kubectl annotate --overwrite "deployment/${DEPLOYMENT}" -n "$NAMESPACE" \
                keel.sh/policy=force keel.sh/trigger=poll keel.sh/pollSchedule="@every 5m"
            notify "⚠️ Reverted jmusicbot to \`${STOCK_IMAGE}\` and added the Keel annotations on the LIVE Deployment, but could not safely edit \`${MANIFEST}\` -- it is unchanged and now out of sync with the cluster. Edit it by hand (stock image + keel.sh annotations) before the next \`kubectl apply\`."
        else
            kubectl apply -f "$MANIFEST"
        fi
        rm -f "$manifest_backup"

        if ! kubectl rollout status "deployment/${DEPLOYMENT}" -n "$NAMESPACE" --timeout="$ROLLOUT_TIMEOUT"; then
            kubectl rollout undo "deployment/${DEPLOYMENT}" -n "$NAMESPACE" || true
            notify "🔴 FAILED to roll out the stock image \`${STOCK_IMAGE}\` (rollout did not become ready within ${ROLLOUT_TIMEOUT}). Rolled back to the previous ReplicaSet; the bot should still be playing the patched build. Cron job NOT removed, so this will retry tomorrow. Check \`kubectl -n ${NAMESPACE} describe deploy/${DEPLOYMENT}\`."
            exit 1
        fi

        commit_manifest "jmusicbot: back to stock ${STOCK_IMAGE}, auto-updates via Keel

arif-banai/MusicBot ${latest_musicbot_tag} bundles youtube-source ${upstream_yts_version} (>= ${FIX_VERSION}).
Committed automatically by scripts/auto-update.sh."

        # Self-uninstall: the transition is one-time, no need to keep checking.
        crontab -l 2>/dev/null | grep -v "auto-update.sh" | crontab - || true

        notify "arif-banai/MusicBot ${latest_musicbot_tag} bundles youtube-source ${upstream_yts_version} -- the itag-18 bug is fixed upstream now. Switched back to \`${STOCK_IMAGE}\`, handed ongoing auto-updates to Keel (\`policy: force\`, poll every 5m, same as pantry-bot), and removed this script's cron job. custom-build/ and the imported \`jmusicbot-custom:*\` images in containerd are left in place for reference; safe to delete (\`sudo k3s ctr images rm ...\`)."
    else
        log "Already on the stock image and upstream has the fix. Nothing to do."
    fi
    exit 0
fi

# --- Step 2: still needs the patch. Is there anything new to rebuild? ---

latest_yts_tag="$(gh_api repos/lavalink-devs/youtube-source/releases/latest | jq -r .tag_name)"
if [ -z "$latest_yts_tag" ] || [ "$latest_yts_tag" = "null" ]; then
    log "ERROR: couldn't determine latest lavalink-devs/youtube-source release, aborting"
    exit 1
fi

desired_state="${latest_musicbot_tag} ${latest_yts_tag}"
current_state="$(cat "$STATE_FILE" 2>/dev/null || echo "")"

if [ "$desired_state" = "$current_state" ]; then
    log "No change since last build (${current_state}). Nothing to do."
    exit 0
fi

log "Rebuilding: MusicBot ${latest_musicbot_tag} + youtube-source ${latest_yts_tag} (previous: ${current_state:-none})"

# Fail fast on the two things that would strand a finished build: no
# passwordless ctr import, or a container name we can't target.
require_ctr_sudo
require_container
warn_on_pull_policy

TMP_BUILD="$(mktemp -d)"
trap 'rm -rf "$TMP_BUILD"' EXIT

if ! git clone --depth 1 --branch "$latest_musicbot_tag" https://github.com/arif-banai/MusicBot.git "$TMP_BUILD" >/tmp/jmusicbot-auto-update-clone.log 2>&1; then
    notify "FAILED to clone MusicBot ${latest_musicbot_tag}. Left the running pod untouched. See /tmp/jmusicbot-auto-update-clone.log on the host."
    exit 1
fi

yts_version_num="${latest_yts_tag#v}"
sed -i "s|<youtube-source.version>.*</youtube-source.version>|<youtube-source.version>${yts_version_num}</youtube-source.version>|" "$TMP_BUILD/pom.xml"

image_tag="jmusicbot-custom:${latest_musicbot_tag}-yts${yts_version_num}"

if ! DOCKER_BUILDKIT=1 docker build -t "$image_tag" "$TMP_BUILD" >/tmp/jmusicbot-auto-update-build.log 2>&1; then
    notify "FAILED to build ${image_tag} (MusicBot ${latest_musicbot_tag} + youtube-source ${yts_version_num}). Left the running pod untouched. See /tmp/jmusicbot-auto-update-build.log on the host."
    exit 1
fi

log "Build succeeded: ${image_tag}"

# --- Step 3: side-load the image into k3s's containerd ---
#
# docker and k3s keep entirely separate image stores. `docker save | k3s ctr
# images import -` is the supported bridge (same command Rancher documents for
# air-gapped installs). ctr normalises the bare name to
# docker.io/library/jmusicbot-custom:<tag>, which is exactly what the kubelet
# resolves `image: jmusicbot-custom:<tag>` to, so the reference matches.
log "Importing ${image_tag} into k3s containerd"
if ! docker save "$image_tag" | sudo -n "$K3S_BIN" ctr images import - >/tmp/jmusicbot-auto-update-import.log 2>&1; then
    notify "FAILED to import \`${image_tag}\` into k3s containerd (\`docker save | sudo k3s ctr images import -\`). The image exists in docker but k3s can't see it, so the pod was left untouched. See /tmp/jmusicbot-auto-update-import.log."
    exit 1
fi

# Only promote the build tree once we know the image is actually deployable.
rm -rf "$BUILD_DIR"
mv "$TMP_BUILD" "$BUILD_DIR"
trap - EXIT

# --- Step 4: record it in git, then deploy ---

manifest_backup="$(mktemp)"
cp "$MANIFEST" "$manifest_backup"
sed -i "s|image: jmusicbot-custom:.*|image: ${image_tag}|" "$MANIFEST"

# The compose version trusted this sed blindly. Here it matters more: the
# manifest is the thing a future `kubectl apply` replays, so a silent no-op
# would quietly roll the bot back to an older build later.
if ! grep -q "image: ${image_tag}" "$MANIFEST"; then
    cp "$manifest_backup" "$MANIFEST"; rm -f "$manifest_backup"
    die "couldn't rewrite the image line in ${MANIFEST} (expected a line matching 'image: jmusicbot-custom:...'). Nothing was deployed."
fi

previous_image="$(kubectl get deployment "$DEPLOYMENT" -n "$NAMESPACE" \
    -o jsonpath="{.spec.template.spec.containers[?(@.name=='${CONTAINER}')].image}" 2>/dev/null || true)"

# `set image`, not `rollout restart` -- see the header. The tag is unique per
# build, so this is what actually triggers a new ReplicaSet.
log "Deploying ${image_tag} to deployment/${DEPLOYMENT} in ${NAMESPACE} (was: ${previous_image:-unknown})"
kubectl set image "deployment/${DEPLOYMENT}" "${CONTAINER}=${image_tag}" -n "$NAMESPACE"

if ! kubectl rollout status "deployment/${DEPLOYMENT}" -n "$NAMESPACE" --timeout="$ROLLOUT_TIMEOUT"; then
    log "Rollout failed; rolling back."
    kubectl rollout undo "deployment/${DEPLOYMENT}" -n "$NAMESPACE" || true
    kubectl rollout status "deployment/${DEPLOYMENT}" -n "$NAMESPACE" --timeout="$ROLLOUT_TIMEOUT" || true
    cp "$manifest_backup" "$MANIFEST"; rm -f "$manifest_backup"
    # State file deliberately NOT written: tomorrow's run retries the same
    # build rather than concluding it already succeeded.
    notify "🔴 FAILED to roll out \`${image_tag}\` (not ready within ${ROLLOUT_TIMEOUT}). Rolled back to \`${previous_image:-the previous ReplicaSet}\` and reverted ${MANIFEST}. Will retry on the next run. Check: \`kubectl -n ${NAMESPACE} logs deploy/${DEPLOYMENT} --previous\` and \`kubectl -n ${NAMESPACE} describe deploy/${DEPLOYMENT}\`."
    exit 1
fi
rm -f "$manifest_backup"

commit_manifest "jmusicbot: ${image_tag}

MusicBot ${latest_musicbot_tag} + youtube-source ${yts_version_num} (upstream still pins ${upstream_yts_version}).
Committed automatically by scripts/auto-update.sh."

# --- Step 5: prune old builds from BOTH image stores ---
#
# The compose version only had docker's store to clean. Now every build
# leaves a copy in containerd too, and those are the big ones -- unpruned
# they'll fill /dev/sda2 in a few months on a daily cron.
docker images jmusicbot-custom --format '{{.Repository}}:{{.Tag}}' \
    | grep -v -F "$image_tag" \
    | xargs -r docker rmi >/dev/null 2>&1 || true

sudo -n "$K3S_BIN" ctr images ls -q 2>/dev/null \
    | grep '^docker\.io/library/jmusicbot-custom:' \
    | grep -v -F "docker.io/library/${image_tag}" \
    | xargs -r sudo -n "$K3S_BIN" ctr images rm >/dev/null 2>&1 || true

echo "$desired_state" > "$STATE_FILE"

notify "Rebuilt and redeployed: MusicBot ${latest_musicbot_tag} + youtube-source ${yts_version_num} (image \`${image_tag}\`, rolled out to deployment/${DEPLOYMENT} in \`${NAMESPACE}\`). Still running the patched build -- upstream MusicBot pom.xml still pins youtube-source ${upstream_yts_version}, below the ${FIX_VERSION} fix."

# ---------------------------------------------------------------------------
# crontab line (user crontab, `crontab -e`) -- unchanged from the Compose era
# except for the path:
#
#   17 4 * * * /home/chase/k8s-homelab/scripts/auto-update.sh >> /home/chase/docker/jmusicbot/auto-update.log 2>&1
#
# The self-uninstall in Step 1 greps for "auto-update.sh", so it still finds
# and removes this line after the path change.
# ---------------------------------------------------------------------------
