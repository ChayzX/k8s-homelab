# Phone → Discord Voice Bridge — Plan & Feasibility

**Status: Phase 1.5 — running the falsifiable spike.** The original
feasibility blocker (below) has been revised by follow-up research; owner
decisions have unblocked a narrower scope. See "Decision log" for what's
settled and what's next. Implementation of the actual bridge has not
started — only a diagnostic spike script exists so far (`phone-bridge/spike/`).

## Phase 0 — Repository & environment discovery

- **Repo / deploy target:** `ChayzX/k8s-homelab`, a bare-metal k3s homelab.
  Workloads are plain Kubernetes manifests per namespace (`opsbot/`,
  `jmusicbot/`, `pantry-bot/`, `minecraft/`, `observability/`), applied with
  `kubectl apply -f`, built via GitHub Actions (`.github/workflows/*-deploy.yml`)
  to GHCR. `git status` was clean on `claude/phone-discord-bridge-mvp-nhwygz`
  before this change — no in-progress user work was at risk.
- **Existing Discord bots:**
  - `opsbot/` — Python, `discord.py>=2.4,<3`, slash-command bot for cluster
    control (`/pods exec`, deployment restarts, Minecraft RCON, `/bug` issue
    filing). Outbound-only connection to Discord's gateway; no inbound
    exposure. User-ID allowlist gate on every command
    (`opsbot/bot/main.py`). This is the closest analog to the moderator
    command surface the bridge needs (`/phone-status`, `/phone-allow`, etc.)
    and the natural place to add them, but it does not touch voice at all
    today.
  - `jmusicbot/` — a Java bot (JDA-based), built and side-loaded as a
    prebuilt image (`jmusicbot-custom:yts1182`); **no source in this repo**.
    It already joins a voice channel to play music, but it's a black box we
    can't extend or inspect for DAVE compatibility, and JDA is a different
    ecosystem from opsbot's Python stack.
  - No existing SIP/telephony code anywhere in the repo.
- **Issue tracking / docs:** GitHub Issues + the GitHub Projects v2 board,
  per `AGENTS.md` / `CLAUDE.md`. This plan lives at
  `docs/phone-discord-bridge-plan.md` (new `docs/` directory).
- **`gh` CLI:** not installed in this remote session; GitHub MCP tools used
  instead for issue/PR operations.

**Conclusion:** if/when implementation proceeds, it belongs as a new
Python service (own namespace/manifests, mirroring `opsbot/`'s layout — an
outbound-only Discord gateway connection plus an inbound SIP trunk is a very
different network/security posture from opsbot's control-plane bot, so it
should be a separate deployment, not a module bolted onto opsbot). No code
changes were made in Phase 0.

## Phase 1 — Feasibility spike: Discord DAVE

Per the coordinator brief: *"Discord voice channels now require
DAVE-compatible end-to-end voice encryption... If Discord DAVE support is
unavailable in the chosen stack, stop and report the blocker with
alternatives. Do not work around it using a self-bot."* That condition is
met for the **receive** direction. Findings below are from current Discord
developer docs, library changelogs/issue trackers, and the DAVE protocol
spec (checked 2026-09-02).

### Timeline
- DAVE (Discord Audio & Video End-to-End Encryption) shipped September 2024,
  externally audited by Trail of Bits, spec open-sourced at
  `discord/dave-protocol`.
- Discord **mandated** DAVE for all voice/video — DMs, group calls, voice
  channels, Go Live — starting **March 1, 2026**. Non-DAVE clients/bots can
  no longer join calls (early enforcement began in February 2026). There is
  no bot exemption; a bot omitting `max_dave_protocol_version` is treated as
  a *downgrade* event (which itself gets announced to the channel), not a
  supported opt-out.

### Send direction (bot speaks the phone caller's audio into the channel)
- `discord.py` added DAVE support in **2.7.0**, calling it explicitly
  "tentative," via a new dependency (`davey`, a Rust/OpenMLS implementation
  with Python bindings by the same author as the PR, merged into
  `discord.py` master 2026-01-07). 2.7.1 added diagnostics
  (`python -m discord --version` now reports the `davey` build) and a hard
  error if `davey` is missing.
- `py-cord` also added send-side DAVE support, and in at least one
  cross-library comparison (an `openclaw`/`discord.js` bug report) was
  observed correctly maintaining a channel's E2EE status where `discord.js`
  broke it — but py-cord has its own open connection-negotiation bug
  (`Pycord-Development/pycord#3135`, error 4017, "In Progress," no fix
  merged as of this check).
- **Verdict: send is plausible but immature.** `discord.py` 2.7.x + `davey`
  is the more credible of the two Python options given `opsbot` is already
  on `discord.py`, but "tentative" is Discord.py's own word and `davey` is a
  new dependency with limited field use. This needs a real proof-of-concept
  against a live Discord voice channel, not just a changelog read, before
  it's trusted.

### Receive direction (bot relays the channel's audio to the phone caller)
This is the hard blocker:
- **`discord.py` has never supported voice receive in core**, DAVE or not —
  a 2021 maintainer decision (`Rapptz/discord.py#1094`) that it's out of
  scope for the library. The only receive path is a third-party extension,
  `discord-ext-voice-recv`, last released **2025-06-18** (v0.5.2a179,
  pre-DAVE), whose own docs call it not-feature-complete with "no
  guarantees... or random breaking changes," and explicitly warn one of its
  core sinks off as "pretty broken... usage is not advised." It shows no
  DAVE/MLS decryption integration at all.
- Under DAVE, decrypting *any* participant's incoming audio requires being a
  full member of the channel's MLS (Messaging Layer Security) group: parsing
  the `Welcome` message to get the group's exported secrets, deriving each
  sender's ratcheted per-frame key, tracking generation/nonce counters, then
  AES-128-GCM-decrypting each frame. This is materially more than RTP/Opus
  handling — it's real protocol-state machinery, not a config flag.
- `davey` (the library `discord.py`'s *send*-side DAVE support depends on)
  is a genuine OpenMLS implementation and, per its own type stubs, is
  crypto-capable of the decrypt side too — but no maintained Python
  (or JS: `discordjs/discord.js#11419`, `#24825` show the same
  "DAVE causes zero audio capture / decryption failure" pattern on the
  `@discordjs/voice` side) library currently wires that decrypt path into a
  usable "receive decoded audio frames from channel" API for bot authors.
  The pieces to build it exist; the integration doesn't.
- **Verdict: receive is not currently feasible with an off-the-shelf,
  reasonably maintained library, in any ecosystem checked (discord.py,
  py-cord, discord.js).** Building it ourselves means implementing the MLS
  client-side state machine against `davey`'s primitives directly — genuine
  protocol-level engineering, not application code.

### Gate outcome

**STOP per the coordinator brief's own instruction.** The MVP as specified
("speak/listen in one configured Discord voice channel") requires the
receive direction, which is blocked. This is not worked around with a
self-bot (ruled out explicitly and independently a bad idea — automating a
real user's account to sidestep E2EE is both against Discord's ToS and a
privacy problem for the three existing users).

### Alternatives, for a Phase 2 decision (none started without approval)

1. **Wait.** Track `discord.py`/`davey` and `py-cord` receive-side DAVE
   work; re-run this spike in a few months. Lowest engineering risk, but no
   MVP now, and the current send-side is only "tentative" too.
2. **One-way MVP first.** Ship send-only: phone caller can be *heard* in
   the Discord channel, but cannot *hear* it back (no receive decode). This
   satisfies half the stated objective and would need explicit user sign-off
   that it's an acceptable interim scope, clearly labeled as such to
   callers and the three Discord users.
3. **Build the receive path ourselves against `davey`.** Highest capability,
   highest cost/risk: real MLS protocol-state code, needs its own security
   review, and per the operating principles is exactly the kind of work that
   requires escalation to a stronger model and cannot be claimed done
   without a real live-call test — no theoretical/design-only success
   claims.
4. **Re-scope the "listen" side away from live Discord voice**, e.g. a
   periodic text/TTS summary of channel activity relayed to the caller
   instead of raw audio. Sidesteps DAVE receive entirely but changes the
   product significantly and needs the user's explicit buy-in.

No telephony provider, phone number, or billable resource has been touched.
No Discord bot token, secret, or channel ID has been created or requested.

## Rejected in this pass

- Extending `jmusicbot` (Java/JDA) instead of a new Python service — ruled
  out because its build isn't in this repo (can't inspect/extend or verify
  its DAVE posture) and it's a different language/runtime than the rest of
  this repo's bot tooling.
- Bolting the bridge onto `opsbot` — ruled out because an inbound SIP trunk
  is a fundamentally different network exposure than opsbot's outbound-only
  gateway connection; keeping them separate keeps opsbot's current, narrow
  attack surface unchanged.

## Decision log (post-blocker)

The owner chose **alternative 3 — build the receive path ourselves**
(ChayzX/k8s-homelab#173). Two follow-up findings and decisions since then:

1. **New Discord Application, not a reused one.** The bridge runs under its
   own bot token/application in the Discord Developer Portal, separate from
   `opsbot`'s and `jmusicbot`'s. Consistent with the Phase 0 conclusion
   above (different network posture, separate service).
2. **Must be free to run.** Discord side already is (existing k3s cluster,
   free gateway API). Telephony/SIP cost is an open question for the
   Telephony Agent phase — flagged, not yet resolved; no billable resource
   created.
3. **Revised feasibility finding (escalated research, Discord Protocol
   Agent, `claude-opus-5`):** the original "no library does receive-side
   DAVE decryption" verdict was too pessimistic **specifically for `davey`**
   (the dependency `discord.py`'s existing tentative *send* support already
   uses). `davey`'s `DaveSession.decrypt(user_id, media_type, packet)` is a
   real, non-stub implementation verified against its Rust source
   (`davey/src/session.rs`, `davey/src/cryptor/decryptor.rs`), and per-sender
   ratchet keys are derived automatically for *every* group member the
   moment the bot joins the call — because deriving other members' keys is
   inherent to correctly joining an MLS group at all (confirmed against the
   DAVE whitepaper: "any member of the group can derive the key for any
   sender"). `discord.py` never calls `decrypt()` only because it has no
   incoming-UDP receive loop (a 2021 decision to keep voice receive out of
   core, three years before DAVE existed) — not because decryption itself
   is unsolved.

   **What's actually missing is ordinary networking code, not
   cryptography:** a UDP receive loop for incoming RTP, SSRC→user_id
   mapping (from voice-gateway SPEAKING events), RTP header stripping
   before handing the payload to `davey.decrypt()`, and Opus decode of the
   plaintext it returns — plus a subclass/hook into discord.py's
   semi-private `voice_client._connection.dave_session` (or a fully custom
   voice client as fallback). Full agent report:
   ChayzX/k8s-homelab#173 (comment).

4. **Owner decisions on the revised scope (both confirmed, proceed):**
   - Proceed on the reduced scope — receive-loop plumbing on top of
     `davey`'s already-working `decrypt()`, not a from-scratch MLS/crypto
     implementation.
   - Accept the security/consent posture: this bridge decrypts other
     Discord participants' audio at a point we control, using `davey` — a
     community reimplementation of DAVE **not covered by** the Trail of
     Bits audit Discord commissioned for its own client and for `libdave`
     itself. That undoes DAVE's core guarantee (intermediaries can't listen
     in) for any call the bridge is in, which affects the three existing
     Discord users too. Owner accepted this explicitly, without a
     separate pre-notification requirement to those users.

## Phase 1.5 — falsifiable spike (in progress)

Per the Discord Protocol Agent's recommendation, the smallest test that can
confirm or kill the revised finding, in order:

1. Connect a bot to a real DAVE voice channel via `discord.py`, then read
   `voice_client._connection.dave_session.get_user_ids()` /
   `get_decryption_stats(other_user_id)` — if another real participant's
   ratchet state already shows up, the headline finding is confirmed with
   **zero MLS code written**.
2. If step 1 succeeds: capture one raw incoming RTP packet from that user,
   strip the RTP header, call
   `dave_session.decrypt(other_user_id, davey.MediaType.audio, payload)` —
   success is real plaintext Opus bytes back.

A diagnostic script for this lives at `phone-bridge/spike/dave_receive_probe.py`
(see its README for how to run it).

**First real run (2026-09-01, `phone-bridge/spike/RESULTS.md`):** voice
connect and the DAVE handshake succeeded; `dave_session` was found exactly
where expected (`voice_client._connection.dave_session`, `ready=true,
status=ACTIVE`); `get_user_ids()` returned two IDs — the bot's own and one
other real participant's. The script itself printed INCONCLUSIVE, but that
was a bug in the script, not a real negative: `get_user_ids()` returns
string snowflakes while the comparison used `member.id` (an int), so the
membership check could never match. The raw session data already showed
the other participant's key present after group join — consistent with the
finding under test — but step 1 didn't reach `get_decryption_stats()`
before returning, and step 2 (real packet decrypt) never ran because step 1
short-circuited. **Fixed** (normalize to string for the membership check,
keep the confirmed IDs as ints for the calls that need them —
`decrypt()`/`get_decryption_stats()` are int-typed per davey's stubs);
`PyNaCl` also added to `requirements.txt` (discord.py's voice code hard-requires
it, the first run had to install it manually to get past a `RuntimeError`).
**Needs a clean re-run** to get a real step 1 PASS and reach step 2.

## Next step

Run `phone-bridge/spike/dave_receive_probe.py` against a real test Discord
server (a throwaway bot application, not opsbot's/jmusicbot's) with at least
one other real participant present and speaking, and report the output back.
That result decides whether Phase 2 design proceeds on the `davey`-based
plumbing plan above, or whether the finding needs revisiting again.
