# scotty — bead-me-up-scotty (beads web UI)

Manifests for the `scotty` namespace: the web UI for the
[beads](https://github.com/gastownhall/beads) (`bd`) tracker, running in k3s at
`scotty.scotty.svc:3000`, exposed at https://bead.greeniespantry.uk via the
(remotely-managed) Cloudflare tunnel in `pantry-bot/60-deployment-cloudflared.yaml`.

- `00-namespace.yaml` — `scotty` namespace.
- `10-deployment.yaml` — the app pod, hostPath mounts, init container.
- `20-service.yaml` — ClusterIP service.
- `config.example.json` — example config.json (mirrors the live schema).

Source checkout (upstream): `/home/chase/bead-me-up-scotty`
(`brendan-appstart/bead-me-up-scotty`). The container image is `bead-me-up-scotty:local`
(built from that checkout; `imagePullPolicy: Never`).

## How config.json is persisted

The app keeps its config (not in beads) at
`~/.config/bead-me-up-scotty/config.json` in the container (config.ts:
`configDir()`). In k3s that path is a hostPath mount to
`/home/chase/.config/bead-me-up-scotty-k8s/config.json`
(scotty/10-deployment.yaml). Two ways it gets written:

1. **The app itself**: Settings UI does `PUT /api/config`, which calls
   `persist()` and writes the file back (lib/config.ts `persist()`). The
   init container `fix-config-perms` chowns the dir so uid 1000 can write it.
2. **Direct hostPath edit**: editing the file on the host and restarting the
   pod (config is read at startup and cached — `getConfig()` caches; the
   running process does not hot-reload a hand-edited file).

Both are the documented intended mechanism. There is no ConfigMap for it —
do not convert this to a ConfigMap without also confirming the app's
`persist()` path (the Settings UI writes back to the same file).

## User model TODAY (evidence from source)

**Short version: scotty has NO per-user identity or auth. It is a single-user
app with one shared write identity, plus a free-form `assignee` string on beads.**

Evidence (file paths relative to the checkout `/home/chase/bead-me-up-scotty`):

- **Explicitly single-user**: README.md "A local, single-user web UI for beads".
- **No auth**: no `middleware.ts`; no cookies, sessions, JWTs, Authorization
  headers, or login flow anywhere under `app/`, `lib/`, `components/` (grep'd
  2026-08-13). Anyone who can reach the URL is "the user".
- **One shared write identity**: every UI write shells out to `bd` with
  `BEADS_ACTOR=<humanActor>` (lib/bd.ts:43). `humanActor` is a single string in
  config.json, defaulting to `os.userInfo().username` or the `BEADS_ACTOR` env
  (lib/config.ts:73). Today it is `"node"` — so every card created/edited in the
  web UI is stamped as `node`, regardless of who clicks.
- **`humanAllowlist` is NOT auth**: it is an array of names used only to classify
  authorship as 👤 human vs 🤖 agent for origin badges / insights / gamification
  (lib/attribution.ts, lib/filters.ts, lib/insights.ts). Live value: `["node"]`.
- **Settings is a global knob**: any visitor can `PUT /api/config` and change
  `humanActor`/`humanAllowlist` for the whole instance (app/api/config/route.ts,
  components/settings-view.tsx). It is "who writes as", not "who may log in".
- **Assignees ARE rendered**: cards show an avatar/initials and the assignee or
  "Unassigned" (components/board/bead-card.tsx); the create modal has an
  assignee dropdown seeded from `{actor, ...existing assignees}`
  (components/create-bead-modal.tsx). Assignee is a free-form string passed
  straight to `bd create/update --assignee` (lib/bd.ts) — there is no user
  registry, no validation, no roster.

Live config (checked 2026-08-13, both the hostPath file and the running pod):

```json
{ "humanActor": "node", "humanAllowlist": ["node"],
  "pollIntervalMs": 30000,
  "projects": [ { "id": "k8s-homelab", "path": "/data/k8s-homelab" },
                { "id": "pantry-bot",  "path": "/data/pantry-bot" } ] }
```

Assignee usage on the boards today:
`k8s-homelab` — 38/75 cards assigned (all to `HomeyBeam`); `pantry-bot` — none.

## What is missing for "each user is their own user"

1. No authentication / authorization. Whoever can load the page is the user.
2. No per-user actor — all UI writes share one `humanActor`.
3. No user registry — `assignee` is a free-form string, not a roster lookup.
4. No per-user views ("my cards") — the UI has filters, not identity-aware views.
5. `humanAllowlist` only controls origin badges, not who may act.

## Options (decision needed)

| Option | What it is | Effort | Notes |
|---|---|---|---|
| **A. Upstream scotty feature** | Per-user identity in scotty: per-request actor and/or real auth | Upstream work (fork or PR to `brendan-appstart/bead-me-up-scotty`) | The "right" fix; needs a design for per-request `BEADS_ACTOR` and/or a login + user roster in config.json |
| **B. External auth (Cloudflare Access)** | Zero Trust / Access policy on bead.greeniespantry.uk so only listed identities reach scotty | Infra-only, in the Cloudflare dashboard; no scotty change | Stops random traffic, but inside Access all users still share one actor unless combined with A |
| **C. Per-user instances** | One scotty Deployment per user, each with its own config.json `humanActor` and its own hostPath config dir, sharing the same bead mounts | N pods; hostPath per user | Zero scotty changes; each user's writes stamped with their own actor; heavy to run/update |
| **D. Convention-only (works today)** | Keep one shared instance; treat the beads `assignee` field as the roster and `humanAllowlist` as the human list | Docs only | Cards are already assignable and rendered; assignees sync to both boards via normal `bd` sync. No identity on writes (all still `node`) |

**Recommendation:** do **D now** (it is the only zero-effort path and matches
beads' design — assignees are names, not accounts), and file **A** as an
upstream feature request (per-user actor / auth) with **B** as the access
gatekeeper once the route needs protecting. Record the decision on
`k8s-homelab-95h`.

## Beads-side: per-user assignee convention (cross-board)

- Assign a card: `bd update <id> --assignee <name>` (or `-a`), or pass
  `--assignee` to `bd create`. Applies to any board (`-C <repo>` for others).
  scotty renders it on the card and offers it in the create/edit dropdown.
- Filter your work: `bd ready --assignee <name>` / `bd ready -u` (unassigned).
- Keep `humanAllowlist` in config.json in sync with the human names you use as
  assignees so origin badges (👤/🤖) stay correct. This is the only "user list"
  scotty has today.
- Known actors: `node` (the scotty machine identity, what UI writes are stamped
  as), `HomeyBeam` (git author / main human assignee on k8s-homelab). Decide the
  canonical per-person name and use it consistently across boards.
