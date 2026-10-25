---
title: HikariCP pool sizing
tags: [jdbc, performance, postgres]
learned: 2026-10-02
---
## TL;DR
A small pool is usually faster. Hikari's rule of thumb: `connections = cores * 2 + effective_spindle_count`, so 10–20 for a typical Postgres box. More connections mostly add context switching on the database side.

## Key points
- Each Postgres connection is a process (~5–10 MB). 200 app pods × 50 connections kills the DB. Use PgBouncer in transaction mode in front.
- `maximumPoolSize` = `minimumIdle` (fixed pool) avoids churn under load.
- Long [[spring-transactional|@Transactional]] methods hold a connection for their whole duration, including HTTP calls inside them. Keep transactions short.
- With [[virtual-threads]] the pool is the new bottleneck: queueing shows up as `connectionTimeout` (default 30 s).

## Example
```yaml
spring:
  datasource:
    hikari:
      maximum-pool-size: 16
      minimum-idle: 16
      connection-timeout: 3000
      leak-detection-threshold: 20000
```

## Pitfalls
- `leak-detection-threshold` is the fastest way to find a forgotten `close()` or a transaction that calls an external API.
