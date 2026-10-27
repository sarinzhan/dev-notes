---
title: Coupling, cohesion and the general design principles
tags: [design-principles, architecture, coupling, cohesion]
learned: 2026-10-27
---
## TL;DR
Almost every design rule reduces to two measurable ideas: **cohesion** (how strongly the parts of one unit belong together) and **coupling** (how much one unit must know about another to work). Good design is high cohesion inside a unit and low coupling between units. KISS, DRY, YAGNI, separation of concerns and the dependency rule are different angles on the same goal: make the next change cheap and local. [[solid]] is the object-level version of the same ideas.

## Key points

### Cohesion
- A unit (class, package, module, service) is cohesive when its elements change for the same reason and serve one purpose. `SimActivationService` with `activate`, `deactivate`, `suspend` is cohesive; `UtilService` with date formatting, SMS sending and PDF export is not.
- Low cohesion shows up as: a class you must read entirely to change one method, test classes with unrelated fixtures, modules whose name is a plural noun (`helpers`, `common`).
- At service level cohesion is what a [[bounded-contexts-and-context-mapping|bounded context]] gives you: one team, one language, one reason to deploy.

### Coupling
| Kind (weakest to strongest) | Example | Cost of change |
|---|---|---|
| Message / event coupling | Service publishes `ProfileActivated`, consumers do not know who published it | A new consumer needs nothing from the producer |
| Data coupling | Method takes the three values it needs | Local |
| Stamp coupling | Method takes a whole `Order` but uses only `order.id()` | Changing `Order` forces recompiles everywhere |
| Control coupling | `boolean sendEmail` flag changes behaviour inside the callee | Caller must know the callee's internals |
| Common coupling | Shared mutable global, shared database table between services | Any change breaks unknown users |
| Content coupling | Reaching into another class's private state, reflection on fields | Everything |
- **Temporal coupling**: A must run before B, but nothing in the types says so (`init()` then `process()`). Fix with constructors, builders or a state machine ([[constructors-and-initialization]]).
- **Deployment coupling**: two services must be released together. The classic microservice failure: a distributed monolith ([[monolith-vs-microservices]]).
- Afferent vs efferent coupling: how many modules depend on you (afferent, Ca) versus how many you depend on (efferent, Ce). Instability `I = Ce / (Ca + Ce)`; stable modules (low I) should be abstract, unstable ones concrete. ArchUnit and `jdeps` measure both.

### The general principles
- **KISS** (keep it simple): the simplest design that meets today's requirements; complexity must be paid for by a concrete need. A `switch` beats a plugin framework with one plugin.
- **DRY** (don't repeat yourself): one authoritative representation of each piece of *knowledge*, not of each line of code. Two services that happen to validate an MSISDN the same way are not duplication; two copies of the tax rule are. Premature DRY creates coupling: a shared `common-lib` that every service depends on is often worse than a little repetition.
- **YAGNI** (you aren't gonna need it): do not build for a requirement that does not exist yet. The abstraction you guess is usually the wrong one and still has to be maintained.
- **Separation of concerns**: each part addresses one concern (HTTP mapping, business rule, persistence). Layers ([[layered-architecture]]), ports and adapters ([[hexagonal-architecture]]) and AOP ([[spring-aop]]) are tools for it.
- **The dependency rule**: source-code dependencies point inward, toward the domain; the domain knows nothing about frameworks, databases or transports. This is the rule behind [[clean-architecture]] and the "D" in [[solid]].
- **Principle of least astonishment**: a `get` method does not mutate, a `save` does not send an email.
- **Law of Demeter** (talk only to your friends): `order.customer().address().city()` couples you to three classes; `order.shippingCity()` couples you to one.
- **Composition over inheritance**: inheritance is the strongest form of coupling; prefer delegation ([[class-relationships]], [[structural-patterns]]).

### How these show up in interviews
- "What is wrong with this `UserService`?" — low cohesion (three responsibilities), common coupling (static config), control coupling (boolean flags).
- "Why not one shared library of entities for all microservices?" — deployment and data coupling; every schema change ripples through every service.
- "When is duplication acceptable?" — when the two copies will change for different reasons; the test is reasons for change, not similarity of text.

## Example
Reducing stamp and control coupling in a notification call. Before: the caller passes a whole aggregate and a flag; after: a small, cohesive value and polymorphism.

```java
// Before: stamp coupling (whole Subscriber) + control coupling (boolean decides the channel)
void notify(Subscriber subscriber, boolean useSms) {
    if (useSms) smsGateway.send(subscriber.getMsisdn(), subscriber.getLocale(), "activated");
    else emailGateway.send(subscriber.getEmail(), subscriber.getLocale(), "activated");
}

// After: data coupling + one channel decides for itself; adding Push adds a class, not a branch
public record Notification(String recipient, Locale locale, String template) {}

public interface NotificationChannel {
    boolean accepts(Subscriber s);
    void send(Notification n);
}

@Service
@RequiredArgsConstructor
public class Notifier {
    private final List<NotificationChannel> channels;

    public void profileActivated(Subscriber s) {
        var n = new Notification(s.preferredContact(), s.locale(), "profile-activated");
        channels.stream().filter(c -> c.accepts(s)).findFirst()
                .orElseThrow(() -> new NoChannelException(s.id()))
                .send(n);
    }
}
```

## Pitfalls
- "Low coupling" taken to mean "an interface for everything": an interface with one implementation and no test double adds indirection without reducing coupling to the behaviour.
- DRY applied across bounded contexts produces a shared kernel nobody can change; see [[bounded-contexts-and-context-mapping]].
- Cohesion by technical type (`controllers/`, `services/`, `repositories/` packages) hides low functional cohesion; package by feature instead ([[modular-monolith]]).
- YAGNI is not an excuse to skip the things that are expensive to add later: observability, idempotency keys, schema `version` fields ([[idempotency]], [[message-schema-evolution]]).
- Related: [[solid]], [[ddd-basics]], [[oop-principles]]; [[monolith-vs-microservices]] applies the same coupling vocabulary to service boundaries.
