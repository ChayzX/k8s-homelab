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

GRANT CONNECT ON DATABASE pantry TO pantry_grafana;
GRANT USAGE ON SCHEMA public TO pantry_grafana;
-- Current PantryBot SQL panels use only these three tables. Do not grant
-- future-table defaults: new panels must receive an explicit privilege review.
GRANT SELECT ON TABLE
  public.pantry_events,
  public.pantry_outbox,
  public.community_participants
TO pantry_grafana;
