---
title: Network access control to database servers
tags: [networking, security, postgres, firewall, pg-hba]
learned: 2026-10-29
---
## TL;DR
A database should be reachable only by the services that need it, over a private network, and never from the internet. Control access in layers: where the server listens (`listen_addresses`), which networks may reach the port (firewall, security groups, private subnets), who may authenticate from where and how (`pg_hba.conf`), whether the traffic is encrypted (TLS), and what each role may do once connected (least privilege). Humans reach production through a bastion or SSH tunnel, not an open port. Every layer assumes the one before it has failed.

## Key points
### Layers, outside in
| Layer | Postgres / infra knob | Blocks |
|---|---|---|
| Network placement | private subnet, no public IP, no route from the internet gateway | the whole internet |
| Packet filter | security group / `nftables`: allow 5432 only from the app subnet or app security group | other hosts in the VPC, lateral movement |
| Listen address | `listen_addresses = '10.20.5.10'` instead of `'*'` | interfaces you did not intend (public NIC, Docker bridge) |
| Host-based auth | `pg_hba.conf`: type, database, user, address, method | wrong client IP, wrong database, weak auth method |
| Encryption | `ssl = on`, `hostssl` lines, `sslmode=verify-full` on the client | sniffing and man-in-the-middle on the path ([[tls-handshake-and-certificates]]) |
| Authorization | roles with `GRANT` on specific schemas, no superuser for apps | a compromised app dropping tables or reading other services' data |

### `listen_addresses` and the port
- Default is `localhost`: only local connections. Setting `'*'` binds every interface, including a public one if the host has it. Bind the private address explicitly when the DB runs on a VM.
- Changing the port from 5432 is not security; scanners find it in seconds. A port closed by the firewall is.
- In Docker, `ports: ["5432:5432"]` publishes on `0.0.0.0` of the host and Docker's iptables rules bypass `ufw`. Use `127.0.0.1:5432:5432` or no published port at all, and let services reach the DB over the Compose network ([[docker-networking]]).

### `pg_hba.conf`
- Each line: `TYPE DATABASE USER ADDRESS METHOD`. The **first** matching line wins, top to bottom; there is no fallthrough. Put specific lines first, a final `reject` line last if you want explicit denial.
- `TYPE`: `local` (Unix socket), `host` (TCP, with or without TLS), `hostssl` (TCP with TLS only), `hostnossl`.
- `METHOD`: `scram-sha-256` (default since Postgres 14; use it), `md5` (legacy), `cert` (client certificate, see [[mtls-and-service-to-service-auth]]), `peer` (OS user = DB user, local only), `reject`, and `trust` (no password; never on TCP).
- Reload with `SELECT pg_reload_conf();` and check what Postgres actually parsed in `pg_hba_file_rules` (errors appear in its `error` column).

### Private subnets, bastion and tunnels
- Typical cloud layout: load balancer in a public subnet, application in a private subnet, database in a separate data subnet with no NAT route out ([[ip-addressing-and-nat]], [[network-routing]]). Managed databases (RDS, Cloud SQL) give the same choice: "publicly accessible: no".
- Humans and migration jobs connect through a bastion host or an SSH local forward: `ssh -L 15432:db.internal:5432 bastion` and then `psql -h 127.0.0.1 -p 15432`. Only the bastion's SSH port is exposed, protected by keys and an audit trail. Cloud alternatives (SSM Session Manager, IAP tunnels, a VPN) avoid even that open port.

### Connection proxies
- PgBouncer (or RDS Proxy, Pgpool-II) sits between apps and Postgres: it multiplexes many client connections onto a small server pool ([[hikaricp-pool-sizing]], [[sockets-and-connection-pooling]]). For access control it becomes the only host in `pg_hba.conf`, so the database accepts exactly one source address.
- The proxy has its own auth (`auth_type = scram-sha-256`, `auth_query`) and its own TLS settings on both sides (`client_tls_*`, `server_tls_*`); terminating TLS at PgBouncer and connecting to Postgres in clear text undoes the encryption on that hop.
- An L4 balancer in front of replicas works too ([[load-balancers-l4-vs-l7]]), but it hides client IPs from `pg_hba.conf` unless PROXY protocol is supported.

### Least-privilege roles
- One role per service, owning only its schema. Migration role (DDL, used by Flyway at deploy) separate from runtime role (DML only) ([[schema-migrations-flyway-liquibase]]).
- `REVOKE CREATE ON SCHEMA public FROM PUBLIC` (the default since Postgres 15), `CONNECT` granted per database, read-only roles for reporting and support.
- Credentials from a secret store, rotated, never in the image ([[secrets-management]]).

## Example
Postgres on a private VM: bind the private address, require TLS and SCRAM from the app subnet, allow only PgBouncer for the runtime role, and verify.
```bash
# postgresql.conf
listen_addresses = '10.20.5.10'
ssl = on
ssl_cert_file = '/etc/postgresql/tls/server.crt'
ssl_key_file  = '/etc/postgresql/tls/server.key'
password_encryption = scram-sha-256

# pg_hba.conf  (first match wins)
# TYPE    DATABASE  USER          ADDRESS          METHOD
local     all       postgres                       peer
hostssl   esim      esim_migrate  10.20.6.0/24     scram-sha-256
hostssl   esim      esim_app      10.20.6.20/32    scram-sha-256   # PgBouncer only
hostssl   esim      esim_readonly 10.20.9.0/24     scram-sha-256   # bastion / BI subnet
host      all       all           0.0.0.0/0        reject

# host firewall: 5432 only from the app and bastion subnets
sudo nft add rule inet filter input tcp dport 5432 ip saddr '{ 10.20.6.0/24, 10.20.9.0/24 }' accept
sudo nft add rule inet filter input tcp dport 5432 drop

# verify from a laptop through the bastion
ssh -fN -L 15432:10.20.5.10:5432 bastion.internal
psql "host=127.0.0.1 port=15432 dbname=esim user=esim_readonly sslmode=require" \
     -c "select rule_number, type, database, user_name, address, auth_method, error from pg_hba_file_rules;"
```
```sql
-- runtime role: data only, own schema only
CREATE ROLE esim_app LOGIN PASSWORD :'app_pw';
GRANT CONNECT ON DATABASE esim TO esim_app;
GRANT USAGE ON SCHEMA esim TO esim_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA esim TO esim_app;
ALTER DEFAULT PRIVILEGES FOR ROLE esim_migrate IN SCHEMA esim
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO esim_app;
```

## Pitfalls
- `host all all 0.0.0.0/0 md5` plus a public IP: internet-wide password guessing, and thousands of exposed Postgres and Redis instances get hijacked every year exactly this way.
- `sslmode=require` encrypts but does not check the certificate; only `verify-full` (JDBC: `sslmode=verify-full`, `sslrootcert=...`) stops a man-in-the-middle.
- A `trust` line left from local debugging that matches a TCP range before the real rules: first match wins.
- Publishing the DB port in Docker Compose "just for local tools" on a shared server: the iptables DNAT rule ignores the host firewall.
- Timeouts instead of "connection refused" usually mean a security group or firewall drop, while `no pg_hba.conf entry for host` means the network is fine and HBA rejected you ([[network-debugging-toolkit]]).
- Related: [[api-security-checklist]], [[owasp-top-10-for-backend]], [[postgres-replication-and-failover]] (replication connections need their own `replication` HBA lines).
