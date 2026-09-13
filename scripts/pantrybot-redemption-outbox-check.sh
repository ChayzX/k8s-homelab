#!/usr/bin/env bash
set -euo pipefail

namespace="${PANTRY_NAMESPACE:-pantry-bot}"
deployment="${PANTRY_WORKER_DEPLOYMENT:-pantry-chat-worker}"

# This is intentionally a read-only check. It prints no payloads, tokens, or
# authorization data; only the durable event/outbox routing state is shown.
kubectl -n "$namespace" exec -i "deploy/$deployment" -- node - <<'NODE'
const { Pool } = require("pg");
const pool = new Pool({ connectionString: process.env.PANTRY_DATABASE_URL });

(async () => {
  const result = await pool.query(`
    SELECT e.event_id, e.created_at, e.status AS event_status,
           COUNT(*) FILTER (WHERE o.target = 'overlay'
                            AND o.status = 'completed')::int AS overlay_completed,
           COUNT(*) FILTER (WHERE o.target = 'twitch.channel_points'
                            AND o.status = 'completed')::int AS channel_points_completed,
           COUNT(*) FILTER (WHERE o.target = 'twitch.chat'
                            AND o.status = 'completed')::int AS chat_completed,
           COALESCE(string_agg(DISTINCT o.target || ':' || o.status, ', '
                               ORDER BY o.target || ':' || o.status), '') AS routes
    FROM pantry_events e
    LEFT JOIN pantry_outbox o ON o.event_id = e.event_id
    WHERE e.event_type = 'channel.channel_points_custom_reward_redemption.add'
    GROUP BY e.event_id, e.created_at, e.status
    ORDER BY e.created_at DESC
    LIMIT 10
  `);
  if (result.rows.length === 0) {
    console.error("No channel-point redemption events found");
    process.exitCode = 2;
  } else {
    console.table(result.rows);
    const incomplete = result.rows.filter((row) =>
      row.event_status !== "completed" ||
      row.overlay_completed < 1 ||
      row.channel_points_completed < 1 ||
      row.chat_completed < 1,
    );
    if (incomplete.length > 0) {
      console.error(`${incomplete.length} redemption(s) missing a completed required route`);
      process.exitCode = 1;
    }
  }
  await pool.end();
})().catch((error) => {
  console.error(error.message);
  process.exitCode = 2;
});
NODE
