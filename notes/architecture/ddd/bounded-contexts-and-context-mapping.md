---
title: Bounded contexts and context mapping
tags: [ddd, architecture, microservices, integration]
learned: 2026-10-27
---
## TL;DR
A **bounded context** is the boundary inside which one model and one ubiquitous language are valid: "subscriber" in provisioning (ICCID, EID, profile state) is a different model from "subscriber" in billing (balance, tariff, debt). A **context map** documents how contexts relate and translate: who owns the contract (upstream/downstream), whether the downstream copies the model (conformist) or protects itself (anti-corruption layer), and where a shared kernel or published language exists. Context boundaries are the best first guess at service boundaries ([[monolith-vs-microservices]]).

## Key points

### Finding the boundaries
- Listen for the same word meaning different things ("activation" of a SIM on the HLR vs activation of a promo), or different words for the same thing. Each meaning belongs to a context.
- One context = one team can own it, one model fits in one head, one deploy unit is plausible. In code a context is a module/package with its own entities, repositories and DTOs ([[modular-monolith]]) or a service with its own database ([[data-modeling-for-services]]).
- Subdomain vs bounded context: a **subdomain** is a part of the business (core: eSIM provisioning; supporting: notifications; generic: auth, billing). A bounded context is a solution boundary; ideally one per subdomain, in legacy systems often several contexts cover one subdomain or one context spans two.
- Core subdomains get the modelling effort and the custom code; generic ones are bought or copied.

### Context map relationships
| Pattern | Who adapts | Use when |
|---|---|---|
| **Partnership** | Both teams coordinate releases | Two core contexts evolving together |
| **Shared kernel** | Both share a small library/schema | Tiny, stable common model (ids, money); dangerous if it grows |
| **Customer/supplier** | Upstream plans for downstream needs | Downstream has influence (internal teams) |
| **Conformist** | Downstream adopts the upstream model as is | Upstream will not change for you, model is acceptable (big vendor API) |
| **Anti-corruption layer (ACL)** | Downstream translates at the boundary | Upstream model is messy or foreign (SM-DP+ ES2+, legacy billing, bank host) |
| **Open host service** | Upstream exposes a stable protocol | Many downstreams; REST/gRPC API with versioning |
| **Published language** | Both speak a documented shared format | Events on Kafka with Avro/Protobuf schema ([[message-schema-evolution]]) |
| **Separate ways** | Nobody integrates | Integration costs more than duplication |
- Upstream/downstream is about who influences whom, not data direction: the HLR is upstream of provisioning even when provisioning sends commands to it, because provisioning must adapt to the HLR's protocol.

### Anti-corruption layer in practice
- An ACL is an adapter (in the [[hexagonal-architecture]] sense) that converts the external model into the domain model: a `SmdpClient` returns `DownloadOrder` domain objects, never ES2+ JSON; a `BillingAcl` maps billing's `subscriber_status` codes into `Subscription` state.
- Keep it one-directional and dumb: translation, defaults, error mapping. Business rules do not belong in it.
- Tests: contract tests against the external format ([[contract-testing-and-wiremock]]), unit tests for the mapping.

### Communication between contexts
- Synchronous (REST/gRPC) when the caller needs the answer now and can tolerate coupling to the upstream's availability ([[service-communication-sync-vs-async]]).
- Asynchronous integration events when the downstream reacts to facts ([[domain-events-and-integration-events]], [[event-driven-architecture]]). Published language + outbox is the usual pair ([[outbox-pattern]]).
- Never share a database table between contexts; that is common coupling and makes the schema the contract ([[coupling-cohesion-and-general-principles]]).

## Example
ACL between the provisioning context and the SM-DP+ server: the external ES2+ representation stays inside the adapter.

```java
// Domain port (provisioning context's language)
public interface ProfileServer {
    DownloadOrder order(Iccid iccid, Eid eid);
}

// Anti-corruption layer: adapter translating to/from the ES2+ vendor model
@Component
@RequiredArgsConstructor
class Es2PlusProfileServer implements ProfileServer {
    private final RestClient rest;

    @Override
    public DownloadOrder order(Iccid iccid, Eid eid) {
        var req = Map.of("iccid", iccid.value(), "eid", eid.value(), "releaseFlag", true);
        Es2Response resp = rest.post().uri("/gsma/rsp2/es2plus/downloadOrder")
                .body(req).retrieve().body(Es2Response.class);
        return switch (resp.header().functionExecutionStatus().status()) {
            case "Executed-Success" -> new DownloadOrder(iccid, new MatchingId(resp.matchingId()), DownloadState.RELEASED);
            case "Failed" -> throw mapError(resp.header().functionExecutionStatus().statusCodeData());
            default -> throw new ProfileServerException("unknown status");
        };
    }

    private ProvisioningException mapError(StatusCodeData d) {
        // vendor codes become domain exceptions; the domain never sees "8.2.1"
        return switch (d.subjectCode() + "/" + d.reasonCode()) {
            case "8.2.1/3.9" -> new ProfileNotFoundException();
            case "8.2.5/3.3" -> new ProfileAlreadyReleasedException();
            default -> new ProfileServerException(d.message());
        };
    }
}
```

## Pitfalls
- One `Subscriber` JPA entity used by provisioning, billing and support: every context's field lands in one table, every change touches every team. Split the model even if the services are not split yet.
- A shared kernel that grows into a `common` module with 200 classes: it quietly becomes a distributed monolith.
- Conformist by accident: copying the vendor's DTOs into your domain because it was faster; the vendor's next API version then rewrites your domain.
- Drawing the context map once and never updating it; it is a living document of team relationships as much as of code.
- Related: [[ddd-basics]], [[aggregates-and-invariants]], [[strangler-fig-migration]] (carving contexts out of a monolith), [[esim-provisioning-platform]].
