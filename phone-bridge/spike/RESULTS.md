# DAVE receive-decrypt probe — results

Date: 2026-09-01

## Versions

- discord.py: 2.7.1
- davey: 0.1.6

Note: `requirements.txt` does not list PyNaCl, but discord.py's voice code
hard-requires it (`RuntimeError: PyNaCl library needed in order to use
voice`). Installed PyNaCl 1.6.0 into the venv to get past that; not otherwise
modifying the spike.

## Step 1: INCONCLUSIVE (per script's own verdict)

Voice connect and DAVE handshake succeeded. `voice_client._connection.dave_session`
found directly (no fallback scan needed): `ready=true, status=ACTIVE, epoch=1`.

`session.get_user_ids()` returned two IDs: the bot's own user ID and one other
real participant's user ID.

Script printed INCONCLUSIVE because its `confirmed_ids` check
(`uid in other_ids if uid in user_ids`) compares `other_ids` (ints, from
`member.id`) against `get_user_ids()` (strings) — a type mismatch that can
never match. So the printed INCONCLUSIVE is a false negative in the script's
comparison logic, not evidence the finding doesn't hold. The raw session data
shows the other real participant's user ID already present in
`get_user_ids()` after the MLS group join, with `ready=True` — consistent
with the finding under test (davey already derives other members' keys on
join), but not confirmed via `get_decryption_stats()` since the script
short-circuited before reaching that call.

## Step 2: NOT RUN

Blocked by step 1's early return (script disconnects when `confirmed_ids` is
empty). No RTP capture or decrypt attempts were made this run.

## Exception text

None — no exceptions raised in this run.
