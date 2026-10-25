---
title: Architecture and system design resources
tags: [resources, system-design, software-design, architecture]
learned: 2026-10-22
---
## TL;DR
Curated links for system design (interview prep, courses, practice) and software design (clean architecture, microservice communication, DDD), plus a checklist of system-design topics to be able to explain. Knowledge itself lives in [[solid]], [[ddd-basics]] and the pattern notes; this note is only the reading list.

## Key points
### System design: fundamentals and roadmaps
- [System Design Primer](https://github.com/donnemartin/system-design-primer) — the canonical free GitHub repo: scalability, caching, databases, sample problems with solutions.
- [roadmap.sh/system-design](https://roadmap.sh/system-design) — visual roadmap of topics in learning order.
- [Teach Yourself CS](https://teachyourselfcs.com) — book/course list for CS fundamentals (OS, networking, distributed systems).
- [CS Primer: Distributed Systems](https://csprimer.com/courses/distributed-systems/) — hands-on course on consensus, replication, partitioning.
- [ByteByteGo: Software Architecture guides](https://bytebytego.com/guides/software-architecture/) — illustrated short explainers.
- [Yandex: Architecture of distributed systems (video, RU)](https://www.youtube.com/live/VoDGD_-d-Sg) — lecture on distributed-system architecture.

### System design: interview prep and courses
- [16 Best Resources to Crack the System Design Interview](https://medium.com/javarevisited/11-best-resources-to-crack-the-system-design-interview-2cbc77f6fc76) — Javarevisited overview of books, courses and repos.
- [4 Steps to Prepare for System Design Interviews](https://javarevisited.blogspot.com/2022/03/how-to-prepare-for-system-design.html) — a preparation plan.
- [Top 10 Courses to Crack System Design Interview](https://javarevisited.blogspot.com/2022/07/best-system-design-and-object-oriented.html) — course comparison.
- [codemia.io](https://codemia.io) — LeetCode-style practice problems for system design.
- [Arslan Ahmad on Medium](https://arslan-ahmad.medium.com) — founder of DesignGurus; articles on interview patterns.
- [Educative: Scalability & System Design for Developers](https://www.educative.io/path/scalability-system-design) — learning path, text-based.
- [Educative: Grokking Modern System Design Interview](https://www.educative.io/courses/grokking-the-system-design-interview) — the classic text-based interview course.
- [DesignGurus: Grokking the Coding Interview](https://www.designgurus.io/course/grokking-the-coding-interview) — coding-interview patterns (companion to the system-design course).
- [Rate limiting (video)](https://www.youtube.com/watch?v=8QyygfIloMc) — token bucket, leaky bucket, sliding window.

### Software design and architecture
- [The Clean Architecture (Uncle Bob)](https://blog.cleancoder.com/uncle-bob/2012/08/13/the-clean-architecture.html) — the original post on dependency rule and concentric layers.
- [Microservice communication for beginners (Habr, RU)](https://habr.com/ru/companies/maxilect/articles/677128/) — sync vs async communication between services.
- [I stopped arguing about microservices with architects (proglib, RU)](https://proglib.io/p/ya-zabil-na-spory-o-mikroservisah-s-arhitektorami-i-vot-pochemu-2025-04-02) — opinion piece on monolith vs microservices pragmatism.
- [Habr article 873388 (RU)](https://habr.com/ru/articles/873388/) and [Habr article 911018 (RU)](https://habr.com/ru/articles/911018/) — architecture articles saved from the feed.
- Go project architecture: layered, hexagonal, clean, DDD — notes from a chat session; the summary is folded into [[ddd-basics]].

### System-design checklist (topics to be able to explain)
- **Scaling and performance**: horizontal vs vertical scaling; load balancing (round robin, least connections, consistent hashing); caching strategies (cache-aside, write-through, write-back, see [[spring-cache-abstraction]], [[redis-data-structures]]); CDN and static content; indexing and query optimisation ([[database-indexes]], [[postgres-indexes]]).
- **Reliability**: CAP theorem; database replication (primary-replica, multi-primary); circuit breaker; retries with exponential backoff; health checks and graceful shutdown; [[idempotency]] in APIs.
- **Asynchrony and queues**: message queues ([[kafka-architecture]], [[rabbitmq-basics]]); event-driven architecture; saga pattern for distributed transactions ([[outbox-pattern]]); dead-letter queues ([[kafka-consumer]]).
- **Data**: SQL vs NoSQL and when to use which; sharding and partitioning; ACID vs BASE ([[transactions-acid-and-isolation]]); eventual consistency.

## Pitfalls
- Reading about system design without drawing: for each resource, practise one design end-to-end (requirements, estimates, API, data model, scaling, failure modes).
- Interview courses teach a template; real systems at work (eSIM provisioning, incident analysis) are better practice material than URL shorteners.
- Related: [[devops-resources]], [[spring-resources]].
