# Vault plan

One line per note: `id — scope`. Status marks: `[x]` exists, `[m]` existed before and moved in the restructure, `[ ]` to write. Every planned note is now written; add new ids here first, then write them.
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
- [x] big-o-and-complexity — time/space, amortized, best/avg/worst, common classes with examples
- [x] sorting-algorithms — comparison sorts, stability, TimSort in Java, counting/radix, when which
- [x] binary-search-and-variants — lower/upper bound, search on answer, rotated arrays
- [x] recursion-and-backtracking — call stack, base cases, subsets/permutations, pruning
- [x] two-pointers-and-sliding-window — the two most common interview patterns
- [x] prefix-sums-and-intervals — prefix/difference arrays, merge intervals, sweep line
- [x] stack-and-monotonic-stack — balanced brackets, next greater element, histogram
- [x] graph-traversal-bfs-dfs — adjacency representations, BFS shortest path, DFS, cycle detection
- [x] topological-sort-and-union-find — Kahn/DFS topo sort, DSU with path compression
- [x] shortest-paths-dijkstra — Dijkstra, Bellman-Ford mention, when BFS is enough
- [x] dynamic-programming — memo vs tabulation, classic problems (knapsack, LCS, coins, LIS)
- [x] greedy-algorithms — exchange argument, intervals scheduling, Huffman mention
- [x] string-algorithms — KMP idea, rolling hash, anagram/frequency tricks
- [x] bit-manipulation — masks, XOR tricks, counting bits
- [x] interview-problem-patterns — cheat sheet: pattern → signal in the problem statement

### data-structures
- [m] data-structures — core data structures overview (moved from java/collections)
- [x] arrays-and-dynamic-arrays — contiguous memory, amortized append, cache friendliness
- [x] linked-lists — singly/doubly, when they beat arrays (rarely), LRU use
- [x] stacks-and-queues — deque, ring buffer, queue via two stacks
- [x] hash-tables — hashing, collisions (chaining/open addressing), load factor, resize
- [x] trees-and-bst — traversal orders, balanced trees (AVL/red-black) at concept level, B-tree link to db indexes
- [x] heaps-and-priority-queues — binary heap ops, top-k, k-way merge
- [x] tries — prefix search, autocomplete
- [x] graphs-representation — adjacency list/matrix, weighted/directed, trade-offs
- [x] bloom-filters-and-probabilistic — bloom, count-min, hyperloglog: where they appear in systems
- [x] lru-cache-design — hashmap + doubly linked list, LinkedHashMap trick

### operating-systems
- [x] operating-system-introduction — what an OS is, its three roles, kernel vs the rest, what a running program gets
- [x] operating-system-architecture — layers, privilege modes, syscalls/interrupts/exceptions, kernel designs
- [x] anatomy-of-a-process — program vs process, memory layout (text/data/bss/heap/stack), execution with the stack
- [x] process-management (merged from processes-and-threads and cpu-scheduling) — PCB, process table, threads and TCB, multi-process vs multi-threaded, shared memory, context switch, TLB flush and ASID, preemption, scheduling algorithms, CPU- vs IO-bound, load, cgroups, race conditions
- [x] storage-management — persistence, HDD (CHS, LBA), SSD (NAND, FTL, wear leveling, over-provisioning), PBA/LBA/FS block, file systems, FAT32 and clusters, partitions and alignment, mkfs and mount, page cache, open() modes, a read layer by layer, IO schedulers
- [x] socket-management (all 5 parts; io-models-blocking-nonblocking-epoll merged in) — socket as fd, listening on IP:port, lifecycle, SYN/accept queues, backlog, SYN flood, SO_REUSEPORT, send/receive buffers, blocking, select, epoll, io_uring
- [x] kernel-vs-user-mode — CPU modes, address-space split, syscall walkthrough, interrupts and page faults, mode vs context switch cost, vDSO, user vs system time
- [x] virtualization-and-containers — multi-boot, hypervisors (type 1/2, VT-x, EPT, virtio), namespaces, cgroups, OverlayFS, container networking, security (moved from docker-fundamentals and linux-architecture-and-kernel)
- [x] compilers-and-linkers — ISAs, toolchain stages, object files, symbol resolution and relocation, PLT/GOT, sonames, mold/lld, ELF/PE/Mach-O, runtimes, JIT and W^X, GC (moved from anatomy-of-a-process)
- [x] memory-management — RAM, CPU↔RAM, alignment, virtual memory, fragmentation, page tables, sharing, copy-on-write (Redis), swap, DMA, O_DIRECT
- [x] stack-vs-heap-memory — merged into anatomy-of-a-process (alias)
- [x] syscalls-and-file-descriptors — user/kernel boundary, fd table, everything is a file
- [x] deadlocks-and-synchronization-primitives — four conditions, prevention/avoidance, mutex vs semaphore at OS level
- [x] signals-and-process-lifecycle — fork/exec, zombies, SIGTERM vs SIGKILL, graceful shutdown in containers
- [x] ipc-mechanisms — pipes, sockets, shared memory, message queues

Later (after April 2027), still under operating-systems:
- [ ] filesystems-and-durability — page cache, write-back, fsync, what "on disk" means for Postgres and Kafka
- [ ] zero-copy-and-mmap — sendfile, memory-mapped files, why Kafka is fast
- [ ] cgroups-and-namespaces — how containers limit CPU and memory, JVM in a container, OOM in a pod
- [ ] performance-tooling — top, strace, perf, flame graphs, /proc: finding what a slow process does
- [ ] resource-limits — ulimit, open files, max threads, ephemeral ports

### concurrency-theory
- [x] race-conditions-and-critical-sections — data race vs race condition, atomicity
- [x] mutex-semaphore-monitor — the three primitives and what each guarantees
- [x] memory-models-and-visibility — reordering, happens-before in general, why volatile exists; link java-memory-model
- [x] amdahls-law-and-parallel-speedup — limits of parallelism, Little's law for queues
- [x] lock-free-and-cas — compare-and-swap, ABA problem; link locks-and-lock-free
- [x] actor-and-message-passing-models — alternatives to shared memory

### computation
- [x] cpu (now in operating-systems, merged from four notes) — components, registers, caches, NUMA, RISC vs CISC, clock and IPC, instruction lifecycle, pipelining and hazards, out-of-order, branch prediction, parallelism, a Java loop cycle by cycle
- [x] cpu-caches-and-cache-lines — L1/L2/L3, locality, false sharing
- [x] compilation-interpretation-jit — AOT vs interpreter vs JIT in general; link jvm-jre-jdk
- [x] numbers-bits-and-floating-point — two's complement, IEEE 754, why 0.1+0.2 != 0.3, BigDecimal for money
- [x] text-encodings-and-unicode — ASCII, UTF-8/16, code points vs chars, normalization

### programming
- [x] programming-paradigms — imperative, OOP, functional, declarative; where Java sits
- [x] type-systems — static/dynamic, strong/weak, generics and variance in general
- [x] immutability-and-side-effects — why immutability helps concurrency and reasoning
- [x] error-handling-strategies — exceptions vs result types, fail-fast, retries at boundaries

### security-basics
- [x] hashing-vs-encryption — one-way vs reversible, salts, bcrypt/argon2 for passwords
- [x] symmetric-and-asymmetric-crypto — AES, RSA/EC, key exchange, signatures, where HMAC fits
- [x] owasp-top-10-for-backend — injection, broken auth, SSRF, deserialization, secrets in logs
- [x] secrets-management — env vars vs vaults, rotation, never in git

## java
(existing section, unchanged; see `notes/java/`)

## spring
(existing section; `testing` grew with junit-and-mockito and testcontainers)
- [x] contract-testing-and-wiremock — consumer-driven contracts, WireMock for HTTP stubs
- [x] testing-async-and-kafka-flows — Awaitility, embedded vs Testcontainers, time control

## databases

### fundamentals
- [m] database-fundamentals
- [m] database-indexes
- [m] transactions-acid-and-isolation
- [m] normalization-and-denormalization
- [m] sql-joins-and-commands
- [m] constraints-and-relationships
- [x] query-planning-and-execution — parser → planner → executor, cost model, statistics
- [x] sql-vs-nosql-choosing — consistency, schema, access patterns, when document/kv/column/graph

### postgres
- [m] postgres-indexes
- [m] postgres-isolation-levels
- [x] explain-analyze — reading plans, seq vs index scan, nested loop/hash/merge join, buffers
- [x] postgres-replication-and-failover — streaming replication, sync/async, replication slots, Patroni idea
- [x] postgres-partitioning — declarative partitioning, pruning, when it helps
- [x] postgres-vacuum-and-bloat — MVCC, dead tuples, autovacuum tuning, wraparound
- [x] postgres-locks — row/table locks, advisory locks, deadlock detection, lock_timeout
- [x] postgres-jsonb-and-full-text — jsonb operators, GIN, tsvector basics
- [x] postgres-performance-checklist — pool, indexes, N+1, statement timeout, pg_stat_statements

### nosql
- [x] redis-data-structures
- [x] redis-patterns-and-persistence — cache, lock (and why Redlock is debated), rate limit, pub/sub, streams, RDB/AOF
- [x] mongodb-basics — documents, indexes, aggregation, transactions, when to use
- [x] column-and-wide-column-stores — Cassandra/ClickHouse idea, write path, use cases
- [x] elasticsearch-basics — inverted index, mappings, when search belongs outside Postgres

### design
- [x] data-modeling-for-services — one DB per service, aggregates as tables, soft delete, audit columns
- [x] schema-migrations-flyway-liquibase — versioned migrations, zero-downtime changes (expand/contract)
- [x] multi-tenancy-strategies — shared schema vs schema-per-tenant vs db-per-tenant
- [x] time-money-and-ids — timestamptz, BigDecimal/numeric, UUID v4 vs v7 vs sequences

## messaging
(existing section, unchanged)

## networking

### fundamentals
- [x] osi-and-tcp-ip-models — layers with real examples, where HTTP/TLS/TCP/IP sit
- [x] tcp-vs-udp — handshake, reliability, flow/congestion control, when UDP
- [x] tcp-handshake-and-connection-states — SYN/ACK, TIME_WAIT, keep-alive, half-open
- [x] ip-addressing-ports-nat — IPv4/IPv6, private ranges, NAT, port binding in containers
- [x] sockets-and-connection-pooling — socket lifecycle, ephemeral ports, why pools exist

### http
- [x] http-fundamentals — request/response, methods, status codes, headers, idempotent methods; link rest-and-http
- [x] http-1-1-vs-2-vs-3 — head-of-line blocking, multiplexing, QUIC
- [x] http-caching — Cache-Control, ETag, Last-Modified, Vary, CDN interaction
- [x] cookies-sessions-and-cors — same-origin policy, preflight, SameSite
- [x] websockets-sse-and-long-polling — real-time options and trade-offs; link spring-reactive-webclient-sse
- [x] grpc-and-protobuf — when binary RPC beats REST, streaming, schema evolution

### security
- [x] tls-handshake-and-certificates — TLS 1.3 handshake, CA chain, SNI, where it fails (corporate proxies)
- [x] mtls-and-service-to-service-auth — client certs, SPIFFE idea, vs API keys
- [x] authentication-vs-authorization — sessions, tokens, JWT vs opaque, OAuth2/OIDC flows; link jwt-authentication
- [x] api-security-checklist — rate limits, input validation, replay protection, HMAC webhooks

### dns-and-routing
- [x] dns-resolution — recursive/authoritative, records, TTL, DNS in Kubernetes
- [x] load-balancers-l4-vs-l7 — algorithms, health checks, sticky sessions; link api-gateway-and-service-discovery
- [x] reverse-proxy-nginx-basics — upstreams, timeouts, buffering, TLS termination
- CDN lives in system-design/building-blocks/cdn (do not duplicate here)

### tools
- [x] linux-networking (stays in devops/linux; link from here)
- [x] network-debugging-toolkit — curl, dig, nc, ss, tcpdump, traceroute, mtr with recipes

## distributed-systems

### fundamentals
- [x] cap-and-pacelc — what each letter really means, partitions are not optional
- [x] consistency-models — linearizable, sequential, causal, eventual, read-your-writes
- [x] time-and-clocks — clock skew, NTP, logical clocks, Lamport/vector, hybrid logical
- [x] failure-modes-and-timeouts — crash, omission, network partition, gray failures, timeouts as the only detector
- [x] delivery-guarantees — at-most/least/exactly-once, why exactly-once is end-to-end idempotency

### consensus
- [x] raft-explained — leader election, log replication, safety; what etcd/Kafka KRaft use
- [x] quorums-and-leader-election — majority math, split brain, leases
- [x] distributed-locks-and-fencing — why a lock alone is unsafe, fencing tokens, ZooKeeper/etcd/Redis trade-offs

### replication
- [x] leader-follower-replication — sync/async, lag, failover, lost writes
- [x] multi-leader-and-leaderless — conflicts, LWW, CRDT mention, Dynamo-style quorums
- [x] read-replicas-and-stale-reads — routing reads, replication lag in practice

### partitioning
- [x] sharding-strategies — range, hash, directory; hot partitions
- [x] consistent-hashing — ring, virtual nodes, rebalancing; Kafka partition assignment analogy
- [x] cross-shard-queries-and-transactions — scatter-gather, 2PC, why avoid

### patterns
- [x] backpressure-and-flow-control — bounded queues, reactive streams, Kafka consumer lag
- [x] leases-heartbeats-and-health — liveness detection, lease renewal
- [x] exactly-once-myth — producer idempotence + consumer dedup; link idempotency, outbox-pattern
- [x] distributed-cache-coherence — invalidation strategies, thundering herd, request coalescing

## system-design

### method
- [m] system-design-basics — hub note and interview framework
- [x] requirements-and-constraints — functional/non-functional, SLOs, clarifying questions
- [x] back-of-envelope-estimation — QPS, storage, bandwidth math with worked examples
- [x] latency-numbers — the latency ladder (cache, memory, SSD, network, cross-region) and what it implies
- [x] api-design-for-system-design — REST vs gRPC vs async, pagination, versioning, idempotent endpoints

### building-blocks
- [x] load-balancing — L4/L7, algorithms, health checks, global LB; link load-balancers-l4-vs-l7
- [x] caching — cache-aside/read-through/write-through/write-behind, TTL, invalidation, stampede
- [x] cdn — edge caching, static vs dynamic, invalidation, signed URLs
- [x] message-queues-and-streams — queue vs log, when Kafka vs RabbitMQ vs SQS; link kafka-architecture
- [x] rate-limiting — token bucket, leaky bucket, sliding window, distributed rate limits with Redis
- [x] storage-choices — RDBMS, KV, document, wide-column, blob, search, time-series: pick by access pattern
- [x] search-and-indexing-services — when to add Elasticsearch, sync via CDC/outbox
- [x] background-jobs-and-schedulers — cron vs queue vs workflow engine; link temporal-concepts
- [x] file-and-blob-storage — S3-style storage, presigned URLs, multipart upload
- [x] notifications-and-push — fan-out, channels, retries, dedup

### scaling
- [x] horizontal-vs-vertical-scaling — stateless services, session externalisation
- [x] database-scaling-path — indexes → read replicas → caching → partitioning → sharding; links to distributed-systems and postgres notes
- [x] read-write-splitting-and-cqrs-lite — routing reads, consistency caveats
- [x] async-processing-and-event-driven-scaling — decouple with events, idempotent consumers, DLQ
- [x] multi-region-basics — active/passive vs active/active, data residency, latency

### case-studies
- [x] design-notification-service — API, Kafka, per-channel workers, retries, idempotency, DLQ
- [x] design-url-shortener — key generation, redirects, caching, analytics
- [x] design-rate-limiter — Redis sliding window, distributed counters
- [x] design-chat-service — websockets, presence, message ordering, storage
- [x] design-news-feed — fan-out on write vs read, caching, ranking
- [x] design-payment-processing — idempotency, ledger, outbox, reconciliation; link emv-card-processing
- [x] design-esim-provisioning — long-running orchestration with Temporal; link esim-provisioning-platform

## architecture

### principles
- [x] solid
- [x] coupling-cohesion-and-general-principles — KISS, DRY, YAGNI, separation of concerns, dependency rule

### styles
- [x] layered-architecture — controller/service/repository, where it breaks
- [x] hexagonal-architecture — ports and adapters, domain in the center, Spring example
- [x] clean-architecture — rings, use cases, entities; how it differs from hexagonal
- [x] modular-monolith — modules, enforced boundaries (Spring Modulith / ArchUnit), when enough
- [x] monolith-vs-microservices — trade-offs, team size, when to split, strangler fig

### ddd
- [m] ddd-basics
- [x] bounded-contexts-and-context-mapping — context map patterns (ACL, shared kernel, conformist)
- [x] aggregates-and-invariants — aggregate root, transactional boundary, one aggregate per transaction
- [x] domain-events-and-integration-events — in-process vs published, link outbox-pattern
- [x] use-cases-and-application-layer — orchestration vs domain logic, commands/queries
- [x] value-objects-and-entities — identity, immutability, validation at construction

### patterns
- [x] creational-patterns
- [x] structural-patterns
- [x] behavioral-patterns

### microservices
- [x] saga
- [x] circuit-breaker
- [x] api-gateway-and-service-discovery
- [x] distributed-tracing
- [x] event-driven-architecture — events vs commands, choreography, schema evolution, consumer coupling
- [x] cqrs — separate read/write models, sync strategies, when it is overkill
- [x] event-sourcing — event store, replay, snapshots, projections, pitfalls
- [x] strangler-fig-migration — incremental monolith decomposition
- [x] service-communication-sync-vs-async — REST/gRPC vs messaging, timeouts, retries budget
- [x] bff-and-api-composition — backend-for-frontend, aggregation at the edge

### messaging
- [x] outbox-pattern
- [x] idempotency
- [x] dead-letter-queues-and-retries — retry topics, backoff, poison messages
- [x] message-schema-evolution — Avro/Protobuf/JSON, compatibility, schema registry

### workflows
- [x] temporal-concepts
- [x] workflow-engines-vs-sagas — Temporal vs Camunda vs hand-rolled state machine

## devops
(existing section; kubernetes added)

### observability (additions)
- [x] observability-three-pillars — logs, metrics, traces: what each answers, correlation by traceId
- [x] slo-sli-and-alerting — error budgets, symptom-based alerts, alert fatigue
- [x] error-tracking-with-sentry — events, issues, sampling, PII scrubbing, self-hosted ops
- [x] grafana-dashboards-and-red-use — RED/USE methods, what a service dashboard must show

## projects
- [x] esim-provisioning-platform
- [x] sebep-incident-analysis
- [x] emv-card-processing
