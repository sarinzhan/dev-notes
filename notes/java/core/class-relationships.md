---
title: Relationships between classes
tags: [oop, uml, composition, aggregation, association]
learned: 2026-10-22
---
## TL;DR
Classes relate to each other in six ways: dependency (uses once), association (has a reference), aggregation (has-a, parts live on their own), composition (has-a, owner controls the parts' lifetime), inheritance (is-a) and interface realization. The strength grows from dependency to composition; picking the weakest relationship that works keeps code loosely coupled.

## Key points
| Relationship | Meaning | UML arrow | Java shape |
|---|---|---|---|
| Dependency | A uses B temporarily: parameter, local variable, return type | dashed arrow | `void pay(PaymentGateway gw)` |
| Association | A holds a reference to B and calls it | solid line | `private final Clock clock;` |
| Aggregation | has-a, weak: the part can exist without the whole and may be shared | hollow diamond at the whole | `Library` holds `List<Book>`; a book can move to another library |
| Composition | has-a, strong: the whole creates and destroys its parts, parts are not shared | filled diamond at the whole | `Book` holds `List<Page>`; a page cannot be torn out and put into another book |
| Inheritance | is-a: subclass extends superclass | solid arrow, hollow head | `class Admin extends User` |
| Realization | class implements an interface | dashed arrow, hollow head | `class JdbcUserRepo implements UserRepository` |

- Aggregation and composition are both special cases of association. The practical test: if deleting the owner must delete the parts, it is composition; if the parts survive, aggregation.
- Association can be unidirectional or bidirectional, and has multiplicity (1, 0..1, *). In JPA these map directly to `@OneToMany`, `@ManyToOne`, etc. ([[jpa-entities-and-relationships]]).

### Composition over inheritance
- Inheritance exposes the subclass to every protected detail of the parent and is fixed at compile time; composition lets you swap the collaborator (through an interface) at runtime and test it with a stub.
- Use inheritance only when the subclass is genuinely substitutable for the parent (Liskov, see [[solid]]). "A `Stack` extends `Vector`" in the JDK is the classic violation: a stack exposes `insertElementAt`.
- Dependency injection ([[spring-ioc-and-di]]) is composition done by a container.

### What is a class, really
- A class is a classification: a named set of objects sharing structure and behaviour. Classification can be by abstraction level (an `Account` abstracts from "savings" and "checking"), which is what inheritance hierarchies express.
- A class with a single method and no state is just a function in disguise; a class with state and no behaviour is a record. Recognizing both avoids ceremony.

## Example
```java
// Composition: Order owns its lines; Aggregation: Order references a Customer that exists on its own
public final class Order {
    private final Customer customer;              // aggregation: shared, lives independently
    private final List<OrderLine> lines = new ArrayList<>(); // composition: created and destroyed with the order
    private final PriceCalculator calculator;     // association via interface, injected

    public Order(Customer customer, PriceCalculator calculator) {
        this.customer = customer;
        this.calculator = calculator;
    }

    public void addLine(Product product, int qty) {   // dependency on Product (parameter only)
        lines.add(new OrderLine(product.sku(), product.priceMinor(), qty));
    }

    public long totalMinor() { return calculator.total(lines); }
}
```

## Pitfalls
- A composition that hands out references to its parts (`getLines()` returning the live list) has silently become aggregation; return an unmodifiable view ([[immutable-objects]]).
- Bidirectional associations double the maintenance: both sides must be updated together. In JPA, forgetting that produces orphan rows or `null` foreign keys.
- Inheritance for code reuse alone ("`Report extends ReportHelper`") leads to fragile base classes; extract the helper and compose.
- Related: [[oop-principles]], [[structural-patterns]] (Decorator, Adapter and Composite are all built on composition).
