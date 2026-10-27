---
title: Linked lists
tags: [data-structures, linked-list, memory, complexity]
learned: 2026-10-27
---
## TL;DR
A linked list is a chain of nodes, each holding a value and a reference to the next (singly) or to both neighbours (doubly). It gives O(1) insert and delete **at a node you already hold**, and O(n) for everything that involves finding a position. Because every node is a separate heap object, it is cache-hostile and in practice loses to [[arrays-and-dynamic-arrays]] almost everywhere. It earns its place in exactly two situations: when you hold node references and splice (LRU caches, intrusive queues) and when you need a structure that never copies.

## Key points

### Variants
| Variant | Per node | Can delete a node given only the node? | Typical use |
|---|---|---|---|
| Singly linked | value + `next` | no (need the predecessor) | stacks, free lists, immutable/persistent lists in FP |
| Doubly linked | value + `next` + `prev` | yes, O(1) | `LinkedList`, `LinkedHashMap` order chain, LRU caches |
| Circular | last points to first | depends | round-robin schedulers, ring buffers conceptually |
| With sentinel head/tail | dummy nodes at both ends | yes | removes all null checks from insert/delete code |

### Operations and complexity
| Operation | Singly | Doubly | `ArrayList` for comparison |
|---|---|---|---|
| Access by index | O(n) | O(n) | O(1) |
| Insert/delete at head | O(1) | O(1) | O(n) |
| Insert/delete at tail | O(1) with tail pointer (delete O(n)) | O(1) | O(1) amortized / O(1) |
| Insert/delete at a known node | O(1) after, O(n) before | O(1) | O(n) |
| Search | O(n) | O(n) | O(n) |
| Memory per element | value + 1 ref (+ header) | value + 2 refs (+ header) | 1 ref, contiguous |

### Memory layout and why it is slow
- Each node is a separate allocation: a Java `LinkedList.Node` is ~24 bytes plus the boxed value plus the object header. Traversal follows pointers to arbitrary heap addresses, so almost every step is a cache miss ([[cpu-caches-and-cache-lines]]).
- An `ArrayList` traversal reads 64-byte cache lines sequentially and the prefetcher stays ahead. Benchmarks regularly show `ArrayList` 5–10× faster for iteration and faster even for insert-in-the-middle up to large sizes because `System.arraycopy` is a tight memmove.
- The GC also pays: many small objects with cross-references cost more to trace than one array ([[garbage-collection]]).

### Where linked lists genuinely win
- **Splicing by node reference**: an LRU cache keeps a `HashMap<K, Node>` and moves a node to the front in O(1) ([[lru-cache-design]]). This is exactly what `LinkedHashMap` does internally ([[hashmap-internals]]).
- **Unbounded queues without resize pauses**: `ConcurrentLinkedQueue` and `LinkedBlockingQueue` grow node by node and support lock-free CAS on head/tail ([[concurrent-collections]], [[lock-free-and-cas]]).
- **Persistent/immutable lists** in functional style: prepending shares the whole tail with the old list, no copy ([[immutability-and-side-effects]]).
- **Hash-table chaining**: buckets are singly linked lists until they tree-ify ([[hash-tables]]).

### Classic interview techniques
- **Fast and slow pointers**: detect a cycle (Floyd), find the middle, find the k-th from the end with two pointers k apart.
- **Reversal in place**: three pointers (`prev`, `cur`, `next`), O(1) extra space.
- **Dummy head**: start with a sentinel so inserting before the first real node is not a special case.
- **Merge two sorted lists**: the building block of merge sort on lists and of k-way merge ([[heaps-and-priority-queues]]).

## Example
A singly linked list with a sentinel head, in-place reversal and cycle detection. Used as an intrusive queue where callers keep node references.

```java
public final class SinglyLinkedList<T> {
    private static final class Node<T> {
        T value; Node<T> next;
        Node(T value, Node<T> next) { this.value = value; this.next = next; }
    }

    private final Node<T> head = new Node<>(null, null); // sentinel: never holds a value
    private Node<T> tail = head;
    private int size;

    public void addLast(T value) {
        tail.next = new Node<>(value, null);
        tail = tail.next;
        size++;
    }

    public T removeFirst() {
        if (head.next == null) throw new NoSuchElementException();
        Node<T> first = head.next;
        head.next = first.next;
        if (first == tail) tail = head;          // list became empty
        size--;
        return first.value;
    }

    public void reverse() {                      // O(n) time, O(1) space
        Node<T> prev = null, cur = head.next;
        tail = cur;
        while (cur != null) {
            Node<T> next = cur.next;
            cur.next = prev;
            prev = cur;
            cur = next;
        }
        head.next = prev;
    }

    static <T> boolean hasCycle(Node<T> start) { // Floyd: slow moves 1, fast moves 2
        Node<T> slow = start, fast = start;
        while (fast != null && fast.next != null) {
            slow = slow.next; fast = fast.next.next;
            if (slow == fast) return true;
        }
        return false;
    }

    public int size() { return size; }
}
```

## Pitfalls
- Reaching for `java.util.LinkedList` "because I insert in the middle": the `get(i)` or iterator walk to reach the middle is O(n) and the node allocations are slower than an `ArrayList` shift. Default to `ArrayList`, and to `ArrayDeque` for queues ([[stacks-and-queues]]).
- `LinkedList.get(i)` inside a `for` loop over indexes is O(n²); use the iterator.
- Recursive algorithms over long lists (recursive reverse, recursive merge) overflow the stack at around 10–20k nodes; write them iteratively ([[recursion-and-backtracking]]).
- Forgetting to update `tail` or `size` when removing the last node is the most common bug in hand-written lists; a sentinel plus a test for the one-element case catches it.
- Related: [[data-structures]] for the overview, [[collections-framework]] for the JDK `LinkedList`/`Deque` API, [[lru-cache-design]] for the one place a doubly linked list is the right answer.
