# Vault plan

One line per note: `id — scope`. Status marks: `[x]` exists, `[m]` moved in the restructure, `[ ]` to write.
Folders must match `sections.json`. Ids are stable: a wikilink never breaks when a note moves between folders.

Ownership rules (write it once, link from everywhere else):
- Caching strategies → `system-design/building-blocks/caching`; Redis and Spring Cache stay implementation notes.
- Replication / sharding theory → `distributed-systems`; Postgres how-to → `databases/postgres`; "how to scale" → `system-design/scaling`.
- Concurrency concepts → `computer-science/concurrency-theory`; Java specifics → `java/concurrency`.
- Data-structure concepts → `computer-science/data-structures`; `HashMap` internals etc. → `java/collections`.
- CDN → `system-design/building-blocks/cdn`; networking only links.
- Observability ops → `devops/observability`; `distributed-tracing` stays in `architecture/microservices`.
- DDD and hexagonal → `architecture/ddd` and `architecture/styles`; project stories link there.

Suggested order for interview prep: architecture/styles + system-design + databases → networking → computer-science/algorithms → distributed-systems.

## computer-science

### algorithms
- [ ] big-o-and-complexity — time/space, amortized, best/avg/worst, common classes with examples
- [ ] sorting-algorithms — comparison sorts, stability, TimSort in Java, counting/radix, when which
- [ ] binary-search-and-variants — lower/upper bound, search on answer, rotated arrays
- [ ] recursion-and-backtracking — call stack, base cases, subsets/permutations, pruning
- [ ] two-pointers-and-sliding-window — the two most common interview patterns
- [ ] prefix-sums-and-intervals — prefix/difference arrays, merge intervals, sweep line
- [ ] stack-and-monotonic-stack — balanced brackets, next greater element, histogram
- [ ] graph-traversal-bfs-dfs — adjacency representations, BFS shortest path, DFS, cycle detection
- [ ] topological-sort-and-union-find — Kahn/DFS topo sort, DSU with path compression
- [ ] shortest-paths-dijkstra — Dijkstra, Bellman-Ford mention, when BFS is enough
- [ ] dynamic-programming — memo vs tabulation, classic problems (knapsack, LCS, coins, LIS)
- [ ] greedy-algorithms — exchange argument, intervals scheduling, Huffman mention
- [ ] string-algorithms — KMP idea, rolling hash, anagram/frequency tricks
- [ ] bit-manipulation — masks, XOR tricks, counting bits
- [ ] interview-problem-patterns — cheat sheet: pattern → signal in the problem statement

### data-structures
- [m] data-structures — core data structures overview (moved from java/collections)
- [ ] arrays-and-dynamic-arrays — contiguous memory, amortized append, cache friendliness
- [ ] linked-lists — singly/doubly, when they beat arrays (rarely), LRU use
- [ ] stacks-and-queues — deque, ring buffer, queue via two stacks
- [ ] hash-tables — hashing, collisions (chaining/open addressing), load factor, resize
- [ ] trees-and-bst — traversal orders, balanced trees (AVL/red-black) at concept level, B-tree link to db indexes
- [ ] heaps-and-priority-queues — binary heap ops, top-k, k-way merge
- [ ] tries — prefix search, autocomplete
- [ ] graphs-representation — adjacency list/matrix, weighted/directed, trade-offs
- [ ] bloom-filters-and-probabilistic — bloom, count-min, hyperloglog: where they appear in systems
- [ ] lru-cache-design — hashmap + doubly linked list, LinkedHashMap trick

### operating-systems
- [ ] processes-and-threads — address space, PCB, context switch cost, user vs kernel threads
- [ ] cpu-scheduling — preemption, priorities, CFS idea, what "load" means
- [ ] virtual-memory-and-paging — pages, page tables, TLB, page faults, swap, OOM killer
- [ ] stack-vs-heap-memory — frames, allocation cost, why stack overflow happens
- [ ] syscalls-and-file-descriptors — user/kernel boundary, fd table, everything is a file
- [ ] io-models-blocking-nonblocking-epoll — blocking, non-blocking, multiplexing (select/poll/epoll), async; link java-nio
- [ ] deadlocks-and-synchronization-primitives — four conditions, prevention/avoidance, mutex vs semaphore at OS level
- [ ] signals-and-process-lifecycle — fork/exec, zombies, SIGTERM vs SIGKILL, graceful shutdown in containers
- [ ] ipc-mechanisms — pipes, sockets, shared memory, message queues

### concurrency-theory
- [ ] race-conditions-and-critical-sections — data race vs race condition, atomicity
- [ ] mutex-semaphore-monitor — the three primitives and what each guarantees
- [ ] memory-models-and-visibility — reordering, happens-before in general, why volatile exists; link java-memory-model
- [ ] amdahls-law-and-parallel-speedup — limits of parallelism, Little's law for queues
- [ ] lock-free-and-cas — compare-and-swap, ABA problem; link locks-and-lock-free
- [ ] actor-and-message-passing-models — alternatives to shared memory

### computation
- [ ] how-cpu-executes-code — fetch/decode/execute, registers, pipelines, branch prediction
- [ ] cpu-caches-and-cache-lines — L1/L2/L3, locality, false sharing
- [ ] compilation-interpretation-jit — AOT vs interpreter vs JIT in general; link jvm-jre-jdk
- [ ] numbers-bits-and-floating-point — two's complement, IEEE 754, why 0.1+0.2 != 0.3, BigDecimal for money
- [ ] text-encodings-and-unicode — ASCII, UTF-8/16, code points vs chars, normalization

### programming
- [ ] programming-paradigms — imperative, OOP, functional, declarative; where Java sits
- [ ] type-systems — static/dynamic, strong/weak, generics and variance in general
- [ ] immutability-and-side-effects — why immutability helps concurrency and reasoning
- [ ] error-handling-strategies — exceptions vs result types, fail-fast, retries at boundaries

### security-basics
- [ ] hashing-vs-encryption — one-way vs reversible, salts, bcrypt/argon2 for passwords
- [ ] symmetric-and-asymmetric-crypto — AES, RSA/EC, key exchange, signatures, where HMAC fits
- [ ] owasp-top-10-for-backend — injection, broken auth, SSRF, deserialization, secrets in logs
- [ ] secrets-management — env vars vs vaults, rotation, never in git

## java
(existing section, unchanged; see `notes/java/`)

## spring
(existing section; `testing` grew with junit-and-mockito and testcontainers)
- [ ] contract-testing-and-wiremock — consumer-driven contracts, WireMock for HTTP stubs
- [ ] testing-async-and-kafka-flows — Awaitility, embedded vs Testcontainers, time control

## databases

### fundamentals
- [m] database-fundamentals
- [m] database-indexes
- [m] transactions-acid-and-isolation
- [m] normalization-and-denormalization
- [m] sql-joins-and-commands
- [m] constraints-and-relationships
- [ ] query-planning-and-execution — parser → planner → executor, cost model, statistics
- [ ] sql-vs-nosql-choosing — consistency, schema, access patterns, when document/kv/column/graph

### postgres
- [m] postgres-indexes
- [m] postgres-isolation-levels
- [ ] explain-analyze — reading plans, seq vs index scan, nested loop/hash/merge join, buffers
- [ ] postgres-replication-and-failover — streaming replication, sync/async, replication slots, Patroni idea
- [ ] postgres-partitioning — declarative partitioning, pruning, when it helps
- [ ] postgres-vacuum-and-bloat — MVCC, dead tuples, autovacuum tuning, wraparound
- [ ] postgres-locks — row/table locks, advisory locks, deadlock detection, lock_timeout
- [ ] postgres-jsonb-and-full-text — jsonb operators, GIN, tsvector basics
- [ ] postgres-performance-checklist — pool, indexes, N+1, statement timeout, pg_stat_statements

### nosql
- [x] redis-data-structures
- [ ] redis-patterns-and-persistence — cache, lock (and why Redlock is debated), rate limit, pub/sub, streams, RDB/AOF
- [ ] mongodb-basics — documents, indexes, aggregation, transactions, when to use
- [ ] column-and-wide-column-stores — Cassandra/ClickHouse idea, write path, use cases
- [ ] elasticsearch-basics — inverted index, mappings, when search belongs outside Postgres

### design
- [ ] data-modeling-for-services — one DB per service, aggregates as tables, soft delete, audit columns
- [ ] schema-migrations-flyway-liquibase — versioned migrations, zero-downtime changes (expand/contract)
- [ ] multi-tenancy-strategies — shared schema vs schema-per-tenant vs db-per-tenant
- [ ] time-money-and-ids — timestamptz, BigDecimal/numeric, UUID v4 vs v7 vs sequences

## messaging
(existing section, unchanged)

## networking

### fundamentals
- [ ] osi-and-tcp-ip-models — layers with real examples, where HTTP/TLS/TCP/IP sit
- [ ] tcp-vs-udp — handshake, reliability, flow/congestion control, when UDP
- [ ] tcp-handshake-and-connection-states — SYN/ACK, TIME_WAIT, keep-alive, half-open
- [ ] ip-addressing-ports-nat — IPv4/IPv6, private ranges, NAT, port binding in containers
- [ ] sockets-and-connection-pooling — socket lifecycle, ephemeral ports, why pools exist

### http
- [ ] http-fundamentals — request/response, methods, status codes, headers, idempotent methods; link rest-and-http
- [ ] http-1-1-vs-2-vs-3 — head-of-line blocking, multiplexing, QUIC
- [ ] http-caching — Cache-Control, ETag, Last-Modified, Vary, CDN interaction
- [ ] cookies-sessions-and-cors — same-origin policy, preflight, SameSite
- [ ] websockets-sse-and-long-polling — real-time options and trade-offs; link spring-reactive-webclient-sse
- [ ] grpc-and-protobuf — when binary RPC beats REST, streaming, schema evolution

### security
- [ ] tls-handshake-and-certificates — TLS 1.3 handshake, CA chain, SNI, where it fails (corporate proxies)
- [ ] mtls-and-service-to-service-auth — client certs, SPIFFE idea, vs API keys
- [ ] authentication-vs-authorization — sessions, tokens, JWT vs opaque, OAuth2/OIDC flows; link jwt-authentication
- [ ] api-security-checklist — rate limits, input validation, replay protection, HMAC webhooks

### dns-and-routing
- [ ] dns-resolution — recursive/authoritative, records, TTL, DNS in Kubernetes
- [ ] load-balancers-l4-vs-l7 — algorithms, health checks, sticky sessions; link api-gateway-and-service-discovery
- [ ] reverse-proxy-nginx-basics — upstreams, timeouts, buffering, TLS termination
- [ ] cdn-link — see system-design/building-blocks/cdn (do not duplicate)

### tools
- [x] linux-networking (stays in devops/linux; link from here)
- [ ] network-debugging-toolkit — curl, dig, nc, ss, tcpdump, traceroute, mtr with recipes

## distributed-systems

### fundamentals
- [ ] cap-and-pacelc — what each letter really means, partitions are not optional
- [ ] consistency-models — linearizable, sequential, causal, eventual, read-your-writes
- [ ] time-and-clocks — clock skew, NTP, logical clocks, Lamport/vector, hybrid logical
- [ ] failure-modes-and-timeouts — crash, omission, network partition, gray failures, timeouts as the only detector
- [ ] delivery-guarantees — at-most/least/exactly-once, why exactly-once is end-to-end idempotency

### consensus
- [ ] raft-explained — leader election, log replication, safety; what etcd/Kafka KRaft use
- [ ] quorums-and-leader-election — majority math, split brain, leases
- [ ] distributed-locks-and-fencing — why a lock alone is unsafe, fencing tokens, ZooKeeper/etcd/Redis trade-offs

### replication
- [ ] leader-follower-replication — sync/async, lag, failover, lost writes
- [ ] multi-leader-and-leaderless — conflicts, LWW, CRDT mention, Dynamo-style quorums
- [ ] read-replicas-and-stale-reads — routing reads, replication lag in practice

### partitioning
- [ ] sharding-strategies — range, hash, directory; hot partitions
- [ ] consistent-hashing — ring, virtual nodes, rebalancing; Kafka partition assignment analogy
- [ ] cross-shard-queries-and-transactions — scatter-gather, 2PC, why avoid

### patterns
- [ ] backpressure-and-flow-control — bounded queues, reactive streams, Kafka consumer lag
- [ ] leases-heartbeats-and-health — liveness detection, lease renewal
- [ ] exactly-once-myth — producer idempotence + consumer dedup; link idempotency, outbox-pattern
- [ ] distributed-cache-coherence — invalidation strategies, thundering herd, request coalescing

## system-design

### method
- [m] system-design-basics — hub note and interview framework
- [ ] requirements-and-constraints — functional/non-functional, SLOs, clarifying questions
- [ ] back-of-envelope-estimation — QPS, storage, bandwidth math with worked examples
- [ ] latency-numbers — the latency ladder (cache, memory, SSD, network, cross-region) and what it implies
- [ ] api-design-for-system-design — REST vs gRPC vs async, pagination, versioning, idempotent endpoints

### building-blocks
- [ ] load-balancing — L4/L7, algorithms, health checks, global LB; link load-balancers-l4-vs-l7
- [ ] caching — cache-aside/read-through/write-through/write-behind, TTL, invalidation, stampede
- [ ] cdn — edge caching, static vs dynamic, invalidation, signed URLs
- [ ] message-queues-and-streams — queue vs log, when Kafka vs RabbitMQ vs SQS; link kafka-architecture
- [ ] rate-limiting — token bucket, leaky bucket, sliding window, distributed rate limits with Redis
- [ ] storage-choices — RDBMS, KV, document, wide-column, blob, search, time-series: pick by access pattern
- [ ] search-and-indexing-services — when to add Elasticsearch, sync via CDC/outbox
- [ ] background-jobs-and-schedulers — cron vs queue vs workflow engine; link temporal-concepts
- [ ] file-and-blob-storage — S3-style storage, presigned URLs, multipart upload
- [ ] notifications-and-push — fan-out, channels, retries, dedup

### scaling
- [ ] horizontal-vs-vertical-scaling — stateless services, session externalisation
- [ ] database-scaling-path — indexes → read replicas → caching → partitioning → sharding; links to distributed-systems and postgres notes
- [ ] read-write-splitting-and-cqrs-lite — routing reads, consistency caveats
- [ ] async-processing-and-event-driven-scaling — decouple with events, idempotent consumers, DLQ
- [ ] multi-region-basics — active/passive vs active/active, data residency, latency

### case-studies
- [ ] design-notification-service — API, Kafka, per-channel workers, retries, idempotency, DLQ
- [ ] design-url-shortener — key generation, redirects, caching, analytics
- [ ] design-rate-limiter — Redis sliding window, distributed counters
- [ ] design-chat-service — websockets, presence, message ordering, storage
- [ ] design-news-feed — fan-out on write vs read, caching, ranking
- [ ] design-payment-processing — idempotency, ledger, outbox, reconciliation; link emv-card-processing
- [ ] design-esim-provisioning — long-running orchestration with Temporal; link esim-provisioning-platform

## architecture

### principles
- [x] solid
- [ ] coupling-cohesion-and-general-principles — KISS, DRY, YAGNI, separation of concerns, dependency rule

### styles
- [ ] layered-architecture — controller/service/repository, where it breaks
- [ ] hexagonal-architecture — ports and adapters, domain in the center, Spring example
- [ ] clean-architecture — rings, use cases, entities; how it differs from hexagonal
- [ ] modular-monolith — modules, enforced boundaries (Spring Modulith / ArchUnit), when enough
- [ ] monolith-vs-microservices — trade-offs, team size, when to split, strangler fig

### ddd
- [m] ddd-basics
- [ ] bounded-contexts-and-context-mapping — context map patterns (ACL, shared kernel, conformist)
- [ ] aggregates-and-invariants — aggregate root, transactional boundary, one aggregate per transaction
- [ ] domain-events-and-integration-events — in-process vs published, link outbox-pattern
- [ ] use-cases-and-application-layer — orchestration vs domain logic, commands/queries
- [ ] value-objects-and-entities — identity, immutability, validation at construction

### patterns
- [x] creational-patterns
- [x] structural-patterns
- [x] behavioral-patterns

### microservices
- [x] saga
- [x] circuit-breaker
- [x] api-gateway-and-service-discovery
- [x] distributed-tracing
- [ ] event-driven-architecture — events vs commands, choreography, schema evolution, consumer coupling
- [ ] cqrs — separate read/write models, sync strategies, when it is overkill
- [ ] event-sourcing — event store, replay, snapshots, projections, pitfalls
- [ ] strangler-fig-migration — incremental monolith decomposition
- [ ] service-communication-sync-vs-async — REST/gRPC vs messaging, timeouts, retries budget
- [ ] bff-and-api-composition — backend-for-frontend, aggregation at the edge

### messaging
- [x] outbox-pattern
- [x] idempotency
- [ ] dead-letter-queues-and-retries — retry topics, backoff, poison messages
- [ ] message-schema-evolution — Avro/Protobuf/JSON, compatibility, schema registry

### workflows
- [x] temporal-concepts
- [ ] workflow-engines-vs-sagas — Temporal vs Camunda vs hand-rolled state machine

## devops
(existing section; kubernetes added)

### observability (additions)
- [ ] observability-three-pillars — logs, metrics, traces: what each answers, correlation by traceId
- [ ] slo-sli-and-alerting — error budgets, symptom-based alerts, alert fatigue
- [ ] error-tracking-with-sentry — events, issues, sampling, PII scrubbing, self-hosted ops
- [ ] grafana-dashboards-and-red-use — RED/USE methods, what a service dashboard must show

## projects
- [x] esim-provisioning-platform
- [x] sebep-incident-analysis
- [x] emv-card-processing
