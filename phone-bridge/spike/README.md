# `phone-bridge/spike` — DAVE receive-decrypt falsifiable test

Not the bridge. This is the smallest test that can confirm or kill the
finding in ChayzX/k8s-homelab#173: that `davey` (the dependency
`discord.py`'s existing send-side DAVE support already uses) already
derives other call members' decryption keys when the bot joins the MLS
group, so building the phone bridge's "hear the channel" side is
receive-loop plumbing on top of an existing `decrypt()`, not a
from-scratch encryption implementation.

**This has not been run against a real Discord voice channel yet.**
`docs/phone-discord-bridge-plan.md` explains why and what runs it needs.

## What it proves, in two steps

1. **Step 1** (safe, read-only): after joining a voice channel, check
   whether the bot's `davey.DaveSession` already lists another real
   participant's user ID with derived decryption stats. If yes, the
   headline finding holds with zero crypto code written.
2. **Step 2** (only if step 1 passes): capture a few real incoming RTP
   packets and call `session.decrypt()` on them. Success = plaintext Opus
   bytes back, no exception. **Decrypted audio content is never logged or
   written to disk** — only pass/fail and byte lengths.

## Before you run it

- Create a **new, throwaway** Discord Application + bot at
  https://discord.com/developers/applications. **Do not reuse opsbot's or
  jmusicbot's token** — this is a one-off diagnostic, not a deployment.
- Invite it to a **test server you control** with only "Connect" +
  "Speak" voice permissions.
- Get a **second real account** (a friend, or your own alt) into the same
  voice channel, talking, during the test window.
- **Never** paste the bot token in chat, a commit, or anywhere logged.
  Export it as an environment variable only, run locally:

  ```bash
  cd phone-bridge/spike
  python3 -m venv .venv && source .venv/bin/activate
  pip install -r requirements.txt

  export DISCORD_BOT_TOKEN='...'          # the throwaway bot's token
  export DISCORD_TEST_CHANNEL_ID='...'    # target voice channel's ID
  python dave_receive_probe.py
  ```

- This needs real outbound UDP to Discord's voice servers. Most sandboxed
  CI/agent containers (this one included) proxy or block that — run it
  from a machine/network with normal outbound UDP. It does **not** need to
  be the eventual homelab deployment target, just somewhere with real
  connectivity.

## What to do with the output

Paste the full console output back. It's built to be diagnostic either
way:

- If step 1 fails or is inconclusive, it prints exactly what it found
  (or didn't) instead of guessing, so the next fix is obvious rather than
  another round of theorizing.
- If step 2 fails, the likely cause (per the research agent's risk notes)
  is an RTP-header-extension or `_rtpsize` framing mismatch — the error
  text distinguishes "we never got a packet" from "we got a packet and
  `decrypt()` rejected it."

The result decides whether Phase 2 design proceeds on the `davey`-based
plumbing plan, or whether the feasibility finding needs revisiting again —
see `docs/phone-discord-bridge-plan.md`.
