---
title: Stack vs heap memory
tags: [operating-systems, memory, stack, heap]
learned: 2026-10-27
---
## TL;DR
The **stack** is a per-thread, LIFO region where each function call pushes a **frame** (arguments, locals, return address) and pops it on return; allocation is one pointer move and lifetime is tied to the call. The **heap** is a shared region for objects whose lifetime outlives the call that created them; allocation goes through an allocator (`malloc`, or the JVM's bump-pointer TLAB) and freeing is manual (C) or by GC (Java). A **stack overflow** happens when frames exceed the fixed stack size, almost always from unbounded recursion. JVM-specific layout (heap generations, metaspace, TLABs) is in [[jvm-memory-areas]].

## Key points

### The stack
- One stack per thread, allocated when the thread starts: Linux main thread default 8 MiB (`ulimit -s`), other threads 1 MiB on JVM (`-Xss`), reserved as virtual memory and committed page by page on touch ([[memory-management]]).
- A frame holds: parameters, local variables, saved registers, return address, and a saved frame pointer. The compiler knows every frame's size at compile time, so entering a function is `sub rsp, N` and leaving is `add rsp, N; ret`.
- Grows downward on x86; a **guard page** below the stack is unmapped, so growing past it faults instead of silently overwriting the heap. The JVM catches that fault and throws `StackOverflowError`.
- Cheap and cache-friendly: the top of the stack is almost always in L1 ([[cpu-caches-and-cache-lines]]). No fragmentation, no synchronization: each thread owns its stack.
- Limits: fixed size, lifetime bound to the call, cannot return a pointer to a local (C bug, impossible in Java because objects are never on the stack unless the JIT's escape analysis puts them there).

### The heap
- One per process, shared by all threads. In C, `malloc`/`free` manage it on top of `brk`/`mmap`; in Java the GC-managed heap is reserved up front (`-Xmx`) and objects are allocated by bumping a pointer inside a thread-local allocation buffer, which makes `new` almost as cheap as a stack push.
- The expensive parts are elsewhere: finding free space in a fragmented C heap, and in Java the cost of keeping objects alive through GC cycles ([[garbage-collection]]). Short-lived objects are nearly free in a generational collector; long-lived ones cost on every full/old collection.
- Shared means synchronized: `malloc` uses per-thread arenas (glibc) to avoid a global lock; the JVM uses TLABs for the same reason. A slow path when the TLAB is exhausted takes a CAS on the shared eden pointer ([[lock-free-and-cas]]).
- Native memory in a JVM process is also heap in the OS sense: metaspace, JIT code cache, direct `ByteBuffer`s, thread stacks all count against the container limit even though they are outside `-Xmx`.

### Comparison
| | Stack | Heap |
|---|---|---|
| Owner | one thread | whole process |
| Allocation | pointer move, O(1) | allocator / TLAB bump; GC on the way out |
| Lifetime | until the function returns | until freed or unreachable |
| Size | fixed per thread (MiB) | up to `-Xmx` / RAM |
| Failure | `StackOverflowError` / `SIGSEGV` | `OutOfMemoryError` / `malloc` returns `NULL` / OOM killer |
| Thread safety | inherent | needs synchronization |
| Locality | excellent | depends on allocation pattern |

### Why stack overflow happens
- Unbounded or very deep recursion: recursive tree walks on a degenerate (linked-list-shaped) tree, recursive JSON mapping of a self-referencing entity, a `toString()`/`hashCode()` cycle between two JPA entities ([[jpa-entities-and-relationships]]), a Spring bean that calls itself through a proxy ([[spring-aop]]).
- Large frames: a method with big local arrays in C; in Java locals are references so frames are small, but JIT-inlined deep call chains and very long lambda/stream pipelines can inflate them.
- Thread count × stack size: 5,000 platform threads × 1 MiB = 5 GiB reserved; not an overflow, but a reason you run out of address space or hit `ulimit -u`. Virtual threads keep their frames on the heap and grow them on demand ([[virtual-threads]]).
- Fix: iterate with an explicit `Deque` instead of recursing, cap recursion depth, or raise `-Xss` only for a dedicated thread (`new Thread(group, task, name, stackSize)`) rather than globally ([[recursion-and-backtracking]]).

## Example
```java
// Depth-first delete of an eSIM profile tree (parent -> children) without recursion:
// explicit stack on the heap, bounded by memory instead of -Xss.
void deleteSubtree(UUID rootId) {
    Deque<UUID> stack = new ArrayDeque<>();
    stack.push(rootId);
    while (!stack.isEmpty()) {
        UUID id = stack.pop();                       // frame equivalent: one UUID, not a method frame
        for (UUID child : repo.childIds(id)) {
            stack.push(child);
        }
        repo.delete(id);
    }
}

// The recursive version is shorter and fine for depth < ~10 000,
// but a corrupted parent_id cycle turns it into a StackOverflowError at runtime:
void deleteSubtreeRecursive(UUID id) {
    repo.childIds(id).forEach(this::deleteSubtreeRecursive);
    repo.delete(id);
}
```
A default 1 MiB stack gives roughly 10–20 thousand simple Java frames; the exact number depends on the JIT, which is why the same code can overflow in a cold interpreter and run fine once compiled.

## Pitfalls
- Treating `StackOverflowError` as an `Exception`: it is an `Error`, catching it leaves the thread in an unknown state; fix the recursion.
- Raising `-Xss` globally to "fix" an overflow: every thread now reserves more virtual memory, and the real bug (a cycle) stays.
- Assuming Java objects live on the heap always: escape analysis may scalar-replace a non-escaping object; benchmarks that "measure allocation" can be optimised away ([[jvm-memory-areas]]).
- Confusing OS heap with Java heap when a container is OOM-killed: `-Xmx` is within limits, but direct buffers (`Netty`, Kafka client) and metaspace pushed RSS over `limits.memory`.
- In C, returning the address of a local, or `alloca`/VLA with user-controlled size: both are stack corruption bugs that Java prevents by design.
- Related: [[memory-management]] for how both regions are backed by pages, [[processes-and-threads]] for why each thread needs its own stack.
