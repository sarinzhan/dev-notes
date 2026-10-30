---
title: Database security and permissions
tags: [databases, security, postgres, permissions, least-privilege]
learned: 2026-10-30
---
## TL;DR
Securing a database is layers: keep it off the public internet, require **TLS** and strong auth (SCRAM, certificates), and give each application only the **privileges** it needs. The usual mistake is one superuser-like account used by every service for everything. Better: separate roles per operation (read, create, update, delete), a migration role that owns the schema, and an app role that can only run DML, so a SQL injection or a compromised service cannot `DROP TABLE` or read other schemas. The REST API in front should never pass user input into SQL text and should map one endpoint to the narrowest role.

## Key points
### Network and transport
- Postgres listens where `listen_addresses` says; bind to private interfaces, firewall 5432, no public IPs ([[network-access-to-databases]]).
- `pg_hba.conf` decides who can connect from where with which method, first match wins: `hostssl app_db app_rw 10.0.0.0/16 scram-sha-256`. Never `trust` outside a local dev box.
- **TLS**: `ssl = on` with a certificate; clients use `sslmode=verify-full` (checks CA and hostname). `require` encrypts but does not verify, so it is open to man-in-the-middle ([[tls-handshake-and-certificates]], [[mtls-and-service-to-service-auth]]).
- Use **SCRAM-SHA-256** (default since 14); MD5 password hashes are deprecated. Rotate credentials through a secret manager ([[secrets-management]]).

### Roles and privileges in Postgres
- Roles are users and groups at once. Privileges: `CONNECT` on database, `USAGE`/`CREATE` on schema, `SELECT/INSERT/UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER` on tables, `USAGE` on sequences, `EXECUTE` on functions.
- Since Postgres 15, ordinary users can no longer create objects in the `public` schema by default (`CREATE` was revoked from `PUBLIC`).
- **Least privilege**: the application role is not the owner of the tables. Owners can `DROP` and `ALTER`; app roles get only DML.
- Pattern: `app_owner` (NOLOGIN, owns schema, used by migrations), `app_rw` (DML), `app_ro` (SELECT, for reports and read replicas), and optionally per-operation roles.
- `ALTER DEFAULT PRIVILEGES` makes future tables created by the owner automatically grant the right privileges to the app roles; otherwise every migration needs GRANTs.
- **Row-level security** (`ENABLE ROW LEVEL SECURITY` + `CREATE POLICY`) filters rows per tenant or user ([[multi-tenancy-strategies]]).
- Predefined roles: `pg_read_all_data`, `pg_write_all_data` (14+), `pg_monitor` for monitoring agents.

### Per-operation pools (from the course)
- Idea: create separate connection pools in the application, each logged in as a role with one kind of privilege: a `reader` pool for GET endpoints, `creator` (INSERT), `updater`, `deleter`. A bug or injection in a GET handler then cannot modify data at all.
- Cost: more pools and connections; often `app_ro` + `app_rw` is a good compromise, with reads routed to replicas ([[read-replicas-and-stale-reads]]).

### REST API and the database
- Parameterized queries only; never build SQL from request fields ([[database-wire-protocols]], [[owasp-top-10-for-backend]]).
- Whitelist sortable/filterable columns; `ORDER BY ${userInput}` is injection too.
- Limit page sizes and statement time (`statement_timeout` per role) so one request cannot run a 10-minute query.
- Do not expose internal ids and error details (SQLSTATE text) to clients; log them.
- Audit: `log_connections`, `pgaudit` for DDL and sensitive tables.

## Example
```sql
-- owner role used only by Flyway
CREATE ROLE app_owner NOLOGIN;
CREATE SCHEMA shop AUTHORIZATION app_owner;

CREATE ROLE app_rw LOGIN PASSWORD :'rw_pw';
CREATE ROLE app_ro LOGIN PASSWORD :'ro_pw';
GRANT CONNECT ON DATABASE shop_db TO app_rw, app_ro;
GRANT USAGE ON SCHEMA shop TO app_rw, app_ro;

-- future tables created by app_owner get the right grants automatically
ALTER DEFAULT PRIVILEGES FOR ROLE app_owner IN SCHEMA shop
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_rw;
ALTER DEFAULT PRIVILEGES FOR ROLE app_owner IN SCHEMA shop
  GRANT USAGE ON SEQUENCES TO app_rw;
ALTER DEFAULT PRIVILEGES FOR ROLE app_owner IN SCHEMA shop
  GRANT SELECT ON TABLES TO app_ro;

ALTER ROLE app_rw SET statement_timeout = '5s';
```

## Pitfalls
- Applications connecting as `postgres` or as the table owner: one injection can drop the schema.
- `sslmode=require` and thinking the connection is verified.
- Forgetting `ALTER DEFAULT PRIVILEGES`: the next migration's table is invisible to the app (permission denied in production).
- Database credentials in `application.yml` in git; use environment-injected secrets.
- Related: [[schema-migrations-flyway-liquibase]], [[api-security-checklist]], [[authentication-vs-authorization]].
