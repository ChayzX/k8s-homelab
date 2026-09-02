"""Falsifiable spike: can we decrypt another participant's DAVE-encrypted
voice audio using davey, the same dependency discord.py's existing
send-side DAVE support already uses?

This does NOT build a phone bridge. It proves or disproves one narrow
claim from ChayzX/k8s-homelab#173: that davey's DaveSession already derives
other call members' decryption keys as a side effect of joining the MLS
group, and that its decrypt() call actually returns plaintext for a real
incoming packet.

Two steps, run in order, each printing a clear PASS/FAIL/INCONCLUSIVE:

  Step 1 (safe, read-only, no audio touched): after joining a voice
  channel, inspect the bot's own dave_session object for state about the
  OTHER members already in the channel. If their user IDs show up with
  decryption stats, the MLS group join already derived their keys -- with
  zero crypto code written by us.

  Step 2 (only runs if step 1 passes): capture a few raw incoming RTP
  packets and attempt davey's decrypt() on them. Success is plaintext
  Opus bytes back with no exception. Decrypted audio content is never
  logged or written to disk -- only byte lengths and pass/fail.

Discord.py's DAVE-receive internals are new (merged Jan 2026) and
undocumented beyond its source, so this script is deliberately defensive:
it checks attribute existence with hasattr/getattr and prints what it
finds rather than assuming exact private API shapes. Run it, then paste
the full output back for analysis -- do not hand-edit around a failure
without understanding why it failed first.

Setup (do this, don't skip):
  1. Create a NEW, throwaway Discord Application + bot at
     https://discord.com/developers/applications -- do NOT reuse opsbot's
     or jmusicbot's token.
  2. Invite it to a TEST server you control, with "Connect" + "Speak"
     voice permissions, nothing else.
  3. Have at least one other real account (a friend, or your own alt) join
     the same voice channel and talk during the test window.
  4. Never paste the bot token in chat or commit it anywhere.
     Run locally:
       export DISCORD_BOT_TOKEN='...'
       export DISCORD_TEST_CHANNEL_ID='...'   # the voice channel's ID
       pip install -r requirements.txt
       python dave_receive_probe.py
  5. This needs real UDP egress to Discord's voice servers -- run it from
     a machine/network that actually has that (most sandboxed CI/agent
     containers do not). It does not need to be the eventual homelab
     deployment target, just somewhere with normal outbound UDP.
"""
from __future__ import annotations

import asyncio
import os
import socket
import struct
import sys
import time

import discord

try:
    import davey
except ImportError:
    davey = None  # reported clearly at runtime, not silently

TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
CHANNEL_ID = os.environ.get("DISCORD_TEST_CHANNEL_ID")

CAPTURE_SECONDS = 15
RTP_HEADER_LEN = 12  # version/flags, marker+payload-type, seq, timestamp, ssrc


def _dave_session_from(voice_client: discord.VoiceClient):
    """Best-effort reach into discord.py's internals per the Discord
    Protocol Agent's research (voice_client._connection.dave_session).
    Falls back to scanning for anything davey-shaped if that path doesn't
    exist on the installed discord.py version, and reports what it finds.
    """
    conn = getattr(voice_client, "_connection", None)
    session = getattr(conn, "dave_session", None) if conn is not None else None
    if session is not None:
        return session, "voice_client._connection.dave_session"

    print("  ! voice_client._connection.dave_session not found as documented.")
    print("    Scanning voice_client and its _connection for a davey-shaped object...")
    candidates = []
    for owner_name, owner in (("voice_client", voice_client), ("voice_client._connection", conn)):
        if owner is None:
            continue
        for attr in dir(owner):
            if attr.startswith("__"):
                continue
            try:
                value = getattr(owner, attr)
            except Exception:
                continue
            if davey is not None and isinstance(value, getattr(davey, "DaveSession", ())):
                candidates.append((f"{owner_name}.{attr}", value))
    if candidates:
        path, session = candidates[0]
        print(f"    Found candidate: {path}")
        return session, path
    return None, None


class ProbeClient(discord.Client):
    def __init__(self, channel_id: int):
        intents = discord.Intents.default()
        intents.voice_states = True
        intents.members = True
        super().__init__(intents=intents)
        self.channel_id = channel_id
        self.ssrc_to_user: dict[int, int] = {}

    async def on_ready(self):
        print(f"Logged in as {self.user} ({self.user.id})")
        channel = self.get_channel(self.channel_id)
        if channel is None:
            print(f"FAIL: could not resolve channel {self.channel_id} -- "
                  "check DISCORD_TEST_CHANNEL_ID and that the bot was invited.")
            await self.close()
            return

        other_members = [m for m in channel.members if not m.bot]
        print(f"Joining voice channel '{channel.name}'. "
              f"Other non-bot members present: {[str(m) for m in other_members]}")
        if not other_members:
            print("WARNING: no other real participant is in the channel yet. "
                  "Step 1 will likely be INCONCLUSIVE, not FAIL -- join with a "
                  "second account before/soon after this connects.")

        voice_client: discord.VoiceClient = await channel.connect()
        self._hook_speaking_events(voice_client)

        # Give the DAVE handshake (Welcome/Commit) a moment to complete.
        await asyncio.sleep(3)

        print("\n--- STEP 1: was another member's decryption state already derived? ---")
        session, path = _dave_session_from(voice_client)
        if session is None:
            print("FAIL: no davey.DaveSession-shaped object found anywhere reachable "
                  "from the VoiceClient. The headline finding (davey already derives "
                  "other members' keys on group join) could not be checked this way "
                  "on this discord.py/davey version -- report this output back.")
            await voice_client.disconnect()
            await self.close()
            return

        print(f"Found DAVE session via {path}: {session!r}")
        for attr in ("epoch", "own_leaf_index", "status", "ready"):
            print(f"  session.{attr} = {getattr(session, attr, '<missing>')}")

        try:
            user_ids = session.get_user_ids()
        except Exception as exc:
            print(f"FAIL: session.get_user_ids() raised: {exc!r}")
            await voice_client.disconnect()
            await self.close()
            return
        print(f"  session.get_user_ids() = {user_ids}")

        # get_user_ids() has been observed returning string snowflakes while
        # decrypt()/get_decryption_stats() are typed to take int -- normalize
        # to str for the membership check, but keep confirmed_ids as the
        # original discord.py ints (member.id) for those downstream calls.
        user_ids_str = {str(uid) for uid in user_ids}
        confirmed_ids = [m.id for m in other_members if str(m.id) in user_ids_str]
        if not confirmed_ids:
            print("INCONCLUSIVE: no other real participant's user ID appeared in "
                  "get_user_ids(). Either no one else was in the channel in time, "
                  "or the finding doesn't hold as expected -- not a pass, not "
                  "conclusively a fail. Re-run with someone already in the channel.")
            await voice_client.disconnect()
            await self.close()
            return

        for uid in confirmed_ids:
            try:
                stats = session.get_decryption_stats(uid)
            except Exception as exc:
                stats = f"<get_decryption_stats raised: {exc!r}>"
            print(f"  user {uid}: get_decryption_stats() = {stats}")

        print(f"PASS (step 1): {len(confirmed_ids)} other real participant(s) already "
              "have derived decryption state, with zero MLS/crypto code written here.")

        print(f"\n--- STEP 2: capture real incoming audio and attempt decrypt() "
              f"for {CAPTURE_SECONDS}s ---")
        await self._attempt_decrypt(voice_client, session, confirmed_ids)

        await voice_client.disconnect()
        await self.close()

    def _hook_speaking_events(self, voice_client: discord.VoiceClient) -> None:
        """Best-effort SSRC->user_id mapping from voice-gateway SPEAKING
        (op 5) events. discord.py doesn't expose this publicly; we wrap
        the websocket's message handler defensively rather than assume
        the exact private method name is stable.
        """
        ws = getattr(voice_client, "ws", None)
        handler_name = "received_message"
        if ws is None or not hasattr(ws, handler_name):
            print("  ! could not hook voice websocket for SSRC mapping "
                  "(voice_client.ws.received_message not found on this "
                  "discord.py version) -- step 2 will report SSRCs as unmapped.")
            return

        original = getattr(ws, handler_name)

        async def _wrapped(msg):
            try:
                if isinstance(msg, dict) and msg.get("op") == 5:  # SPEAKING
                    data = msg.get("d", {})
                    ssrc, user_id = data.get("ssrc"), data.get("user_id")
                    if ssrc is not None and user_id is not None:
                        self.ssrc_to_user[int(ssrc)] = int(user_id)
            except Exception as exc:
                print(f"  ! speaking-event hook error (non-fatal): {exc!r}")
            return await original(msg)

        setattr(ws, handler_name, _wrapped)

    async def _attempt_decrypt(self, voice_client, session, confirmed_ids) -> None:
        sock: socket.socket | None = getattr(voice_client, "socket", None)
        if sock is None:
            print("FAIL (step 2): voice_client.socket not found -- can't capture "
                  "raw RTP on this discord.py version. Step 1's result still stands.")
            return

        sock.setblocking(False)
        loop = asyncio.get_event_loop()
        deadline = time.monotonic() + CAPTURE_SECONDS
        packets_seen = 0
        decrypt_attempts = 0
        decrypt_successes = 0
        last_error = None

        while time.monotonic() < deadline:
            await asyncio.sleep(0.05)
            try:
                data = sock.recv(4096)
            except (BlockingIOError, InterruptedError):
                continue
            except OSError as exc:
                last_error = exc
                continue
            if not data or len(data) <= RTP_HEADER_LEN:
                continue
            packets_seen += 1

            # RTP fixed header: version/flags(1) marker+payload-type(1)
            # sequence(2) timestamp(4) ssrc(4), all big-endian.
            _version_flags, _marker_pt, _seq, _timestamp, ssrc = struct.unpack_from(
                ">BBHII", data, 0
            )
            payload = data[RTP_HEADER_LEN:]

            user_id = self.ssrc_to_user.get(ssrc)
            if user_id is None:
                # Not yet mapped, or belongs to a member we didn't confirm
                # in step 1 -- skip rather than guess.
                continue
            if user_id not in confirmed_ids:
                continue

            decrypt_attempts += 1
            try:
                plaintext = session.decrypt(user_id, davey.MediaType.audio, payload)
                decrypt_successes += 1
                print(f"  decrypt OK: user={user_id} ssrc={ssrc} "
                      f"ciphertext_len={len(payload)} plaintext_len={len(plaintext)} "
                      "(content not logged)")
            except Exception as exc:
                last_error = exc
                print(f"  decrypt FAILED: user={user_id} ssrc={ssrc} "
                      f"ciphertext_len={len(payload)} error={exc!r}")

        print(f"\nStep 2 summary: {packets_seen} RTP packets seen, "
              f"{decrypt_attempts} decrypt attempts on confirmed users, "
              f"{decrypt_successes} succeeded.")
        if decrypt_attempts == 0:
            print("INCONCLUSIVE: no RTP packets from a confirmed user were captured "
                  "(SSRC mapping empty, or no one spoke during the window, or "
                  f"speaking-event hook didn't fire). SSRC map collected: "
                  f"{self.ssrc_to_user}. Last socket error: {last_error!r}")
        elif decrypt_successes == decrypt_attempts:
            print("PASS (step 2): every captured packet from a confirmed real "
                  "participant decrypted without error.")
        else:
            print(f"PARTIAL/FAIL (step 2): {decrypt_attempts - decrypt_successes} "
                  "of the attempts raised an error -- see above, and report the "
                  "exception text back for analysis (likely an RTP-header-extension "
                  "or _rtpsize framing mismatch, per the research agent's risk notes).")


def main() -> int:
    if not TOKEN:
        print("Set DISCORD_BOT_TOKEN (a throwaway test bot's token, never committed).")
        return 2
    if not CHANNEL_ID:
        print("Set DISCORD_TEST_CHANNEL_ID to the target voice channel's ID.")
        return 2
    if davey is None:
        print("davey is not installed -- pip install -r requirements.txt first.")
        return 2

    client = ProbeClient(int(CHANNEL_ID))
    client.run(TOKEN)
    return 0


if __name__ == "__main__":
    sys.exit(main())
