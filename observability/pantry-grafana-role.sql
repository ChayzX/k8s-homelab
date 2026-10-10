-- Issue #414: least privilege for Home Grafana's PantryPostgres datasource.
-- Execute as the pantry superuser on Oracle's primary, database pantry.
-- This file never stores a password. Set the login password separately after
-- this migration, then update the Home Grafana Kubernetes Secret.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pantry_grafana') THEN
    CREATE ROLE pantry_grafana NOLOGIN;
  END IF;
END
$$;

ALTER ROLE pantry_grafana
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  INHERIT CONNECTION LIMIT 10;
ALTER ROLE pantry_grafana SET default_transaction_read_only = on;
ALTER ROLE pantry_grafana SET statement_timeout = '15s';

REVOKE ALL PRIVILEGES ON DATABASE pantry FROM pantry_grafana;
REVOKE ALL PRIVILEGES ON SCHEMA public FROM pantry_grafana;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM pantry_grafana;
-- Remove column grants left by an earlier version of this migration. A
-- table-level REVOKE does not remove column-level privileges.
REVOKE SELECT (status) ON public.pantry_outbox FROM pantry_grafana;
REVOKE SELECT (user_id) ON public.community_participants FROM pantry_grafana;

GRANT CONNECT ON DATABASE pantry TO pantry_grafana;
GRANT USAGE ON SCHEMA public TO pantry_grafana;
-- Both live PantryBot dashboards use these columns. Column grants let Grafana
-- aggregate usage while withholding command responses and actor identifiers
-- that its panels never need. COUNT(*) works with column-level SELECT on a
-- table. Review a new panel before adding any table or column privilege.
-- No future-table default privileges are granted.
GRANT SELECT (created_at, source, event_type)
  ON public.pantry_events TO pantry_grafana;
GRANT SELECT ("at", source, reason, action)
  ON public.engagement_actions TO pantry_grafana;
GRANT SELECT (name, enabled, category, permission_level, source, use_count, created_at)
  ON public.custom_commands TO pantry_grafana;
GRANT SELECT (day, status, "count")
  ON public.engagement_message_counts TO pantry_grafana;
GRANT SELECT ("at", action)
  ON public.community_actions TO pantry_grafana;
