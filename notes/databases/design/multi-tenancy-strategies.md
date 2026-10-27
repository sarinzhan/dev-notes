---
title: Multi-tenancy strategies: shared schema, schema per tenant, database per tenant
tags: [multi-tenancy, data-modeling, postgres, saas]
learned: 2026-10-27
---
## TL;DR
A multi-tenant service keeps data of many customers (tenants: operators, partners, companies) in one deployment. Three ways to isolate it: a **shared schema** with a `tenant_id` column on every table (cheapest, scales to millions of tenants, isolation is only as good as your `WHERE` clauses, so enforce it with row-level security), a **schema per tenant** inside one database (clean separation, per-tenant migrations, breaks down past a few hundred tenants), and a **database per tenant** (strongest isolation, independent backup and scaling, highest operational cost, needed for regulated or very large tenants). Most systems start shared and move specific tenants out as they grow; the tenant id must be resolved once per request and never trusted from the payload.

## Key points

### Comparison
| | Shared schema (`tenant_id` column) | Schema per tenant | Database per tenant |
|---|---|---|---|
| Isolation | logical, by query predicate (plus RLS) | namespace; one DB user per schema possible | physical; separate credentials, backups, encryption keys |
| Tenants supported | millions | tens to hundreds (catalog bloat, `pg_dump` time, autovacuum per table × tenants) | tens to hundreds (connection pools per DB, operational overhead) |
| Migrations | one run | one run per schema, must handle partial failure | one run per database, versions drift |
| Noisy neighbour | shared tables, indexes, pool; one big tenant hurts all | shared instance resources | fully separate, can move to its own cluster |
| Cross-tenant queries (analytics, admin) | trivial | `UNION ALL` over schemas | export to a warehouse |
| Per-tenant customization | `jsonb` or feature flags | custom columns/tables possible | anything |
| Cost per tenant | lowest | low | high (minimum instance footprint, pool, monitoring) |
| Connection pooling | one pool | one pool with `SET search_path` per request (`search_path` is per session: must reset, breaks with PgBouncer transaction mode) | pool per tenant or dynamic routing `DataSource` |

### Shared schema done safely
- `tenant_id` is `NOT NULL` on every tenant-owned table and is the **leading column of every composite index and unique constraint** (`UNIQUE (tenant_id, external_ref)`), which also makes it a natural partition and shard key later ([[postgres-partitioning]], [[sharding-strategies]]).
- Resolve the tenant from the authenticated principal (JWT claim, API key, subdomain), store it in a request-scoped context, and apply it in the data layer automatically: Hibernate `@TenantId` (Hibernate 6) or a filter, Spring Data specification, or a `WHERE tenant_id = :tenant` added by a repository base class. Never read it from a request body or query parameter ([[authentication-vs-authorization]]).
- **Row-level security** as the safety net: `ALTER TABLE orders ENABLE ROW LEVEL SECURITY` with a policy `USING (tenant_id = current_setting('app.tenant_id')::bigint)`, and the application sets `SET LOCAL app.tenant_id = ...` at the start of each transaction. A forgotten `WHERE` then returns nothing instead of another tenant's rows. RLS adds a predicate to every plan; the leading `tenant_id` index keeps it cheap. The application role must not be the table owner or a superuser (they bypass RLS unless `FORCE ROW LEVEL SECURITY`).
- Foreign keys should include `tenant_id` (`FOREIGN KEY (tenant_id, customer_id) REFERENCES customers (tenant_id, id)`) so a row cannot reference another tenant's parent.

### Schema per tenant
- `CREATE SCHEMA tenant_42` with identical tables; the request sets `search_path` or the connection is bound to the schema. Hibernate supports `MultiTenantConnectionProvider` with `SCHEMA` strategy; Flyway runs with `schemas` set per tenant in a loop.
- Good for tens of enterprise tenants that want their own data boundary without the cost of a database each, or for legacy per-customer customizations. Watch the catalog: 300 tenants × 200 tables × 5 indexes is 300 000 relations; `pg_dump`, autovacuum scheduling and the planner's catalog cache all slow down.

### Database per tenant
- Required when contracts or regulation demand it (data residency, separate encryption keys, a customer-managed backup), or when one tenant's load would need its own cluster anyway.
- Routing: `AbstractRoutingDataSource` keyed by tenant with a HikariCP pool per database (pool count × pool size connections, see [[hikaricp-pool-sizing]]), or a connection-string catalog loaded at startup with lazy pool creation and eviction. Migrations become a fleet operation with a version per database recorded centrally.
- Often combined: shared schema for the long tail, dedicated databases for the few large or regulated tenants, with the same application code behind a routing layer ("hybrid" or "cell-based" deployment, [[multi-region-basics]]).

### Cross-cutting concerns
- Caches, Kafka topics and search indices need the tenant in the key (`cache:tenant:42:tariffs`, index per tenant or a `tenant` keyword field, [[elasticsearch-basics]]); a shared cache without the tenant in the key is the easiest way to leak data.
- Rate limits, quotas and SLOs per tenant; one tenant's batch job must not consume the whole thread pool or connection pool (bulkheads, [[rate-limiting]]).
- Background jobs iterate tenants explicitly and set the context per tenant; a scheduler without a tenant context is where RLS saves you.
- Testing: every repository test runs with two tenants and asserts the other tenant's data is invisible.

## Example
```sql
-- V7__orders_tenant_rls.sql: shared schema with row-level security as a safety net
ALTER TABLE orders ADD COLUMN tenant_id bigint NOT NULL;
CREATE INDEX CONCURRENTLY orders_tenant_created_idx ON orders (tenant_id, created_at DESC);
ALTER TABLE orders ADD CONSTRAINT orders_tenant_ref_uq UNIQUE (tenant_id, external_ref);

ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders FORCE  ROW LEVEL SECURITY;          -- applies to the owner too
CREATE POLICY orders_tenant_isolation ON orders
    USING (tenant_id = current_setting('app.tenant_id', true)::bigint)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::bigint);
GRANT SELECT, INSERT, UPDATE, DELETE ON orders TO app_rw;   -- app_rw is not the owner
```
```java
// Set the tenant on the connection at the start of every transaction.
// The aspect runs inside the transaction interceptor (lower precedence = later), so the
// SET LOCAL lands on the same connection the repository calls will use.
@Aspect
@Component
@Order(Ordered.LOWEST_PRECEDENCE - 10)
@RequiredArgsConstructor
public class TenantTransactionAspect {
    private final JdbcTemplate jdbc;

    @Before("@annotation(org.springframework.transaction.annotation.Transactional)"
          + " || @within(org.springframework.transaction.annotation.Transactional)")
    public void applyTenant() {
        long tenantId = TenantContext.required();                  // from the JWT, set by a filter
        jdbc.execute("SET LOCAL app.tenant_id = " + tenantId);     // SET LOCAL ends with the transaction
    }
}
```
With `SET LOCAL` the setting dies at commit, so a pooled connection never carries a tenant into the next request; a query without `WHERE tenant_id` still returns only that tenant's rows.

## Pitfalls
- `SET` instead of `SET LOCAL` on a pooled connection: the next request on that connection inherits the previous tenant.
- Unique constraints without `tenant_id`: tenant A's `external_ref = 'INV-1'` blocks tenant B's invoice.
- Schema-per-tenant with PgBouncer in transaction mode: `search_path` set on one server connection applies to whoever gets it next.
- A superuser or table-owner application role silently bypasses RLS; the safety net was never there.
- Related: [[data-modeling-for-services]] for the base schema conventions, [[sharding-strategies]] for when `tenant_id` becomes the shard key, [[api-security-checklist]] for tenant resolution at the edge.
