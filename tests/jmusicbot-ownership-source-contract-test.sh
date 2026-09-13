#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
patch="$root/scripts/patches/jmusicbot-active-active.patch"

require() {
  local fragment="$1"
  if ! grep -Fq -- "$fragment" "$patch"; then
    echo "jmusicbot-ownership-source-contract-test: missing $fragment" >&2
    exit 1
  fi
}

# A lost witness lease must fence the Discord side effect first. The callback
# must also stop renewal and remove the marker that authorizes R2 sync.
require 'LOG.error("JMusicBot witness lease lost; shutting down Discord before another site can take ownership")'
require 'onLost.run();'
require 'stop();'
require 'Files.deleteIfExists(OWNERSHIP_MARKER);'
require 'bot.shutdown();'
require 'InstanceLock.release();'

# The callback is deliberately registered after acquisition; the initial
# acquisition error path may release the lock without ever opening Discord.
callback="$(awk '/ownership.start\(\(\) -> \{/ {inside=1} inside {print} inside && /^            \}\);$/ {exit}' "$patch")"
if ! grep -Fq 'Files.deleteIfExists(OWNERSHIP_MARKER)' <<<"$callback" ||
   ! grep -Fq 'bot.shutdown();' <<<"$callback"; then
  echo "jmusicbot-ownership-source-contract-test: lease-loss callback does not fence Discord/R2 state" >&2
  exit 1
fi

echo "jmusicbot-ownership-source-contract-test: all assertions passed"
