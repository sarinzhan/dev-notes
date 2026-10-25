---
title: OOP principles and the kinds of polymorphism
tags: [oop, polymorphism, encapsulation, inheritance, abstraction]
learned: 2026-10-22
---
## TL;DR
Object-oriented programming models a program as objects, each an instance of a class, with classes forming an inheritance hierarchy. The four principles are abstraction, encapsulation, inheritance and polymorphism. Polymorphism is wider than "a subclass overrides a method": it is a family of mechanisms (parametric, subtype/inclusion, overloading, coercion) that let one piece of code work with several types.

## Key points
### The four principles
- **Abstraction**: expose only the essential characteristics of an object and hide implementation details. In Java this is done with abstract classes and interfaces ([[abstract-classes-and-interfaces]]).
- **Encapsulation**: data and the methods that operate on it live in one object; the internal state is protected and reachable only through a public API (getters/setters, or better, behaviour-oriented methods). Access modifiers enforce it ([[modifiers-final-static-access]]).
- **Inheritance**: a new class is built on an existing one, reusing its code and adding properties or methods. Vocabulary: parent = superclass = base class; child = subclass = derived class.
- **Polymorphism**: the same program fragment behaves "in a similar way" with data of different types. See below.

### Class, object, instance
- A **class** is the description (template, contract) of a set of objects that share attributes and operations. After compilation it is bytecode in a `.class` file, loaded by a class loader at runtime.
- An **object** is a concrete representation of that abstraction: it has identity, state and behaviour, and lives on the heap ([[jvm-memory-areas]]).
- "Instance of a class" and "object" are interchangeable terms. The source's claim that "an object is the bytecode produced from a class" is wrong: bytecode describes the class; objects exist only at runtime.

### Methods, constructors and operators are just functions
- A method is syntactic sugar over a function that receives a hidden first argument `this`. Method references make this visible: `String::length` is a `Function<String, Integer>` whose argument is the receiver.
- A constructor is a function that initializes an object ([[constructors-and-initialization]]); an operator is a function with special syntax.

### Polymorphism, properly
- Pierce's definition: "a family of mechanisms that allow the same program fragment to be used with different types in different contexts".
- Monomorphic function: works with one concrete type. Polymorphic function: works with several.
- Strachey (1967) split it into **parametric** (the code is written generically, abstracting from the concrete types) and **ad-hoc** (there is no single rule for the result type; a call is dispatched to one of several implementations chosen per argument type).
- Cardelli and Wegner (1985) refined this into two families:

| Family | Kind | Java mechanism | Open to new types? |
|---|---|---|---|
| Universal | parametric | [[generics]] (`List<T>`) | Yes: any type satisfying the bounds works, no change to the function |
| Universal | inclusion (subtype) | inheritance and interfaces, overriding | Yes: a new subclass just works |
| Ad-hoc | overloading | several methods with the same name ([[methods-overloading-overriding]]) | No: a new type needs a new overload |
| Ad-hoc | coercion | implicit widening, autoboxing ([[casting-and-autoboxing]]) | No: the conversions are fixed by the language |

- "Universal/open" means the user can add supported types without touching the function's source; "ad-hoc/closed" means the function (or its set of overloads) must be edited. Duck typing (dynamic languages) and algebraic data types (sealed interfaces + records + pattern matching `switch`, Java 21) are other members of the same family.
- When an interviewer asks "what is polymorphism", the expected Java answer is subtype polymorphism via overriding plus a mention of overloading; the classification above is the bonus.

## Example
```java
// Universal (inclusion) polymorphism: one call site, many implementations
sealed interface PaymentMethod permits Card, Wallet {}
record Card(String pan) implements PaymentMethod {}
record Wallet(String phone) implements PaymentMethod {}

interface FeeCalculator { long feeMinor(long amountMinor); }

class CardFee implements FeeCalculator {
    public long feeMinor(long amountMinor) { return amountMinor * 2 / 100; }
}
class WalletFee implements FeeCalculator {
    public long feeMinor(long amountMinor) { return 0; }
}

// Ad-hoc (overloading): the compiler picks the method by static argument type
class Formatter {
    String format(Card c)   { return "card ****" + c.pan().substring(12); }
    String format(Wallet w) { return "wallet " + w.phone(); }
}
```

## Pitfalls
- Overloading is resolved at **compile time** by the static type; overriding at **run time** by the actual object. `format((PaymentMethod) card)` does not compile against the class above because no overload takes `PaymentMethod`.
- Getters and setters on every field are not encapsulation; exposing a mutable `List` through a getter breaks it just as much as a public field. See [[immutable-objects]].
- Deep inheritance hierarchies couple subclasses to superclass internals; prefer composition ([[class-relationships]]) and keep inheritance for genuine "is-a".
- Related: [[solid]] for design principles that build on these, [[class-relationships]] for association, aggregation and composition.
