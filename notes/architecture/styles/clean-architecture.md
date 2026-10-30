---
title: Clean architecture — rings, the dependency rule, use cases
tags: [architecture, clean-architecture, design-principles, java]
learned: 2026-10-27
---
## TL;DR
Clean architecture (Robert C. Martin, 2012) arranges code in concentric rings — entities, use cases, interface adapters, frameworks and drivers — with one **dependency rule**: source code dependencies point inwards only; nothing in an inner ring knows a name from an outer ring. Each use case is an explicit **interactor** class with an input port (what it accepts) and an output port (how it reports back). It is [[hexagonal-architecture]] with more vocabulary and an explicit use-case layer; the added ceremony is worth it in a large, long-lived domain and a trap in a 2,000-line service.

## The problem it solves
- **Without it:** even a service with ports and adapters has no agreed place for application logic: `ProfileService` mixes activation, suspension and reporting in 40 methods, and nothing stops a use case from returning `ResponseEntity` or taking a JPA entity. Over time, dependencies drift outwards again.
- **What it changes:** the dependency rule fixes the direction (inner rings never know outer names), and each user intention becomes its own interactor behind an input port, such as `ActivateProfileUseCase`, so the code reads as a list of use cases.
- **When you don't need it:** a small CRUD service where every use case is "load, set field, save"; there the ceremony costs more than it protects (see the over-engineering trap below).

## Key points
### The rings
| Ring | Contains | Spring equivalent |
|---|---|---|
| Entities | Enterprise business rules: aggregates, value objects, domain services ([[ddd-basics]]) | Plain Java, no annotations |
| Use cases | Application business rules: one interactor per user intention; orchestrates entities and gateways | Plain classes exposed as beans |
| Interface adapters | Controllers, presenters, gateways: convert between the use-case format and the external format | `@RestController`, `@Repository` implementations, Kafka listeners, mappers |
| Frameworks and drivers | Spring, Hibernate, Kafka client, Temporal SDK, the database | Starters, auto-configuration ([[spring-boot-starters-and-autoconfiguration]]) |


### The dependency rule
- An inner ring may not import or be shaped by an outer ring: no `@Entity` on a domain class, no use case returning `ResponseEntity` or catching `DataIntegrityViolationException`.
- Data crossing a boundary is simple structures (records, DTOs) defined by the inner ring. Passing a JPA entity into a use case breaks the rule even if it compiles.
- Crossing inward against the flow of control is done with interfaces the inner ring owns and the outer ring implements — the same dependency inversion as hexagonal ports ([[solid]]).

### Use case interactors and boundaries
- **Input port** (input boundary): an interface with one method, e.g. `ActivateProfileUseCase.activate(ActivateProfileCommand)`. The controller depends on it, not on the interactor class.
- **Interactor**: the implementation; validates the command, loads entities through gateways, applies rules, saves, reports the result.
- **Output port** (output boundary): either a return value (a result record, the pragmatic choice in Java) or a presenter interface the interactor calls, Martin's original form, useful when one use case must produce several views without knowing them.
- **Gateways**: outbound interfaces owned by the use-case ring (`ProfileRepository`, `SmDpPlusGateway`), implemented by adapters.
- One interactor per use case keeps names honest: `CancelOrder`, not `OrderService.cancel` among 40 other methods ([[layered-architecture]]).

### Clean vs hexagonal vs onion
- Hexagonal: two sides (driving and driven), ports and adapters, no prescribed inner structure. Clean: the same inversion, plus the explicit split between entities and use cases and the naming of input/output ports and presenters.
- Onion (Palermo, 2008): the same rule with the labels domain model → domain services → application services → infrastructure.
- In practice teams mix them: hexagonal module layout, clean naming for use cases, DDD aggregates in the centre. What matters in an interview is explaining the dependency rule, not the label.

### The over-engineering trap
- A small CRUD service ends up with `CreateUserCommand`, `CreateUserUseCase`, `CreateUserInteractor`, `CreateUserPresenter`, `UserGateway`, `UserEntity`, `UserJpaEntity`, `UserMapper` — eight files for an `INSERT`.
- Too much when every use case is "load, set field, save" and the entities ring has no methods. Worth it when rules change independently of transport and storage, when several channels (REST + Kafka + Temporal) drive the same use case, or when the domain outlives the framework version.
- Middle path: keep the dependency rule and plain-Java domain, skip presenters, return records from interactors, merge the two outer rings into one `adapter` package.

## Example
One use case with explicit boundaries; the controller depends only on the input port.

```java
// use-case ring
public record ActivateProfileCommand(String iccid, String msisdn) {}
public record ActivateProfileResult(String iccid, ProfileState state, Instant activatedAt) {}

public interface ActivateProfileUseCase {                     // input port
    ActivateProfileResult activate(ActivateProfileCommand cmd);
}
public interface ProfileRepository {                          // gateway owned by the use-case ring
    Optional<Profile> byIccid(Iccid iccid);
    void save(Profile profile);
}
public interface HlrGateway { void activate(Msisdn msisdn, Iccid iccid); }

public class ActivateProfileInteractor implements ActivateProfileUseCase {
    private final ProfileRepository profiles;
    private final HlrGateway hlr;
    private final Clock clock;

    public ActivateProfileInteractor(ProfileRepository profiles, HlrGateway hlr, Clock clock) {
        this.profiles = profiles; this.hlr = hlr; this.clock = clock;
    }

    @Override
    public ActivateProfileResult activate(ActivateProfileCommand cmd) {
        Iccid iccid = Iccid.of(cmd.iccid());                   // value object validates the format
        Profile profile = profiles.byIccid(iccid).orElseThrow(() -> new ProfileNotFound(iccid));
        profile.activate(Msisdn.of(cmd.msisdn()), clock.instant());   // entity enforces "not twice"
        hlr.activate(profile.msisdn(), iccid);
        profiles.save(profile);
        return new ActivateProfileResult(iccid.value(), profile.state(), profile.activatedAt());
    }
}
```

```java
// interface-adapter ring
@RestController
@RequestMapping("/profiles")
@RequiredArgsConstructor
class ProfileController {
    private final ActivateProfileUseCase activate;             // the interface, never the interactor

    @PostMapping("/{iccid}/activate")
    ActivateProfileResponse activate(@PathVariable String iccid, @RequestBody ActivateRequest body) {
        var result = activate.activate(new ActivateProfileCommand(iccid, body.msisdn()));
        return ActivateProfileResponse.from(result);
    }
}
```

The interactor is tested with an in-memory `ProfileRepository`, a recording `HlrGateway` and a fixed `Clock`, without a Spring context ([[junit-and-mockito]]).

## Pitfalls
- Annotating entities with `@Entity` "to save a mapper" moves Hibernate into the innermost ring; lazy proxies and the persistence context then leak into business rules ([[jpa-persistence-context-and-entity-lifecycle]]).
- Use cases that throw framework exceptions or return `ResponseEntity` break the rule from the inside; define domain exceptions and map them in `@ControllerAdvice`.
- `@Transactional` on an interactor puts a Spring annotation in the use-case ring; accept it, or wrap the interactor in a transactional decorator in the adapter ring ([[spring-transactional]]).
- Over-abstraction in small services (see above); if the only implementation of an interface is a one-line pass-through, delete the interface.
- Related: [[hexagonal-architecture]] (same rule, fewer words), [[layered-architecture]] (what this replaces), [[modular-monolith]] (boundaries between features rather than rings), [[ddd-basics]].
