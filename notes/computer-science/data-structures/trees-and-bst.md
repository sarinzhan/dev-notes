---
title: Trees, binary search trees and balanced trees
tags: [data-structures, trees, bst, red-black-tree, b-tree]
learned: 2026-10-27
---
## TL;DR
A tree is a hierarchy of nodes with one root and no cycles. A binary search tree (BST) keeps `left < node < right`, so search, insert and delete cost O(height): O(log n) when the tree is balanced, O(n) when it degenerates into a chain. Self-balancing trees (red-black, AVL) rotate on insert/delete to keep the height logarithmic, which is what `TreeMap`/`TreeSet` give you: sorted keys, range queries and floor/ceiling in O(log n). On disk the same idea becomes a B-tree with wide nodes sized to a page, which is why every database index is a B-tree ([[database-indexes]]).

## Key points

### Vocabulary
- **Root**, **leaf** (no children), **depth** (edges from the root), **height** (longest root-to-leaf path), **subtree**.
- **Binary tree**: at most two children. **Complete**: all levels full except possibly the last, filled left to right (this is what a heap is, [[heaps-and-priority-queues]]). **Full**: every node has 0 or 2 children. **Perfect**: full and all leaves at the same depth, n = 2^h − 1.
- A tree is a connected acyclic graph with n − 1 edges; general graphs are in [[graphs-representation]].

### Traversals
| Order | Visit sequence | Gives you |
|---|---|---|
| Pre-order | node, left, right | copy/serialize a tree, prefix expression |
| In-order | left, node, right | **sorted output for a BST** |
| Post-order | left, right, node | delete children before parent, evaluate expression trees, compute sizes |
| Level-order (BFS) | by depth, left to right | shortest path in an unweighted tree, printing by level |

Recursive traversals use the call stack (depth = height, fine for balanced trees, risky for degenerate ones); iterative ones use an explicit stack or queue ([[stacks-and-queues]], [[recursion-and-backtracking]]).

### BST operations
| Operation | Balanced | Degenerate | How |
|---|---|---|---|
| search / insert | O(log n) | O(n) | walk down comparing keys |
| delete | O(log n) | O(n) | 0 children: unlink; 1 child: splice; 2 children: replace with in-order successor (min of right subtree), delete that |
| min / max | O(log n) | O(n) | leftmost / rightmost |
| floor / ceiling / range `[a, b)` | O(log n + k) | O(n) | walk down, then in-order from the found node |
| k-th smallest | O(log n) with subtree sizes | O(n) | order-statistic tree |

Inserting already sorted keys into a naive BST produces a linked list, the standard trap.

### Self-balancing trees
| Tree | Invariant | Height bound | Rebalance cost | Where |
|---|---|---|---|---|
| **AVL** | heights of the two subtrees differ by at most 1 | ≈ 1.44 log₂ n | up to O(log n) rotations on delete | read-heavy in-memory indexes |
| **Red-black** | nodes red/black; root black; no red child of red; equal black count on every root-to-null path | ≤ 2 log₂(n+1) | ≤ 2 rotations per insert, ≤ 3 per delete | `TreeMap`, `TreeSet`, `HashMap` treeified buckets, Linux CFS scheduler, C++ `std::map` |
| **Treap / skip list** | randomized | expected O(log n) | simple | `ConcurrentSkipListMap` (lock-free sorted map) |
| **Splay** | move accessed node to root | amortized O(log n) | — | caches with locality |

Red-black trades slightly worse balance for cheaper writes, so it is the default for general-purpose libraries. A rotation is an O(1) pointer rewrite that preserves in-order and lowers one subtree by one level.

### B-trees and B+-trees
- A B-tree is a balanced search tree with very wide nodes (hundreds of keys, one node = one disk page), so it stays 3–4 levels deep for billions of keys; a B+tree keeps values only in linked leaves. This is the structure behind database indexes; the full treatment (search, splits, B-tree vs B+tree, page size, MySQL vs Postgres) lives in [[b-tree-vs-b-plus-tree]].
- LSM trees (RocksDB, Cassandra) are the write-optimized alternative ([[database-engines]]).

### Other trees you will meet
- **Trie**: a tree over characters for prefix search ([[tries]]).
- **Segment tree / Fenwick tree**: range sums and updates in O(log n) ([[prefix-sums-and-intervals]]).
- **Merkle tree**: hashes of hashes for integrity checks (git objects, Cassandra anti-entropy repair, blockchains).
- **Expression / AST trees**: compilers, SQL planners ([[query-planning-and-execution]]).

### Java API
- `TreeMap`/`TreeSet` (red-black, `NavigableMap`: `floorKey`, `ceilingKey`, `subMap`, `headMap`, `descendingMap`). Keys need `Comparable` or a `Comparator`; a comparator inconsistent with `equals` makes the map treat "equal" keys as the same. See [[collections-framework]].
- `ConcurrentSkipListMap` for a concurrent sorted map ([[concurrent-collections]]).

## Example
A minimal BST with iterative search, recursive insert and delete via in-order successor; no balancing, which is what the interview usually asks before "and how would you balance it?".

```java
public final class IntBst {
    private static final class Node {
        int key; Node left, right;
        Node(int key) { this.key = key; }
    }
    private Node root;

    public boolean contains(int key) {
        Node n = root;
        while (n != null) {
            if (key == n.key) return true;
            n = key < n.key ? n.left : n.right;
        }
        return false;
    }

    public void insert(int key) { root = insert(root, key); }
    private Node insert(Node n, int key) {
        if (n == null) return new Node(key);
        if (key < n.key) n.left = insert(n.left, key);
        else if (key > n.key) n.right = insert(n.right, key);
        return n;                                   // duplicate: ignore
    }

    public void delete(int key) { root = delete(root, key); }
    private Node delete(Node n, int key) {
        if (n == null) return null;
        if (key < n.key) n.left = delete(n.left, key);
        else if (key > n.key) n.right = delete(n.right, key);
        else {
            if (n.left == null) return n.right;     // 0 or 1 child
            if (n.right == null) return n.left;
            Node succ = n.right;                    // 2 children: in-order successor
            while (succ.left != null) succ = succ.left;
            n.key = succ.key;
            n.right = delete(n.right, succ.key);
        }
        return n;
    }

    public void inOrder(IntConsumer visit) {        // sorted ascending
        Deque<Node> stack = new ArrayDeque<>();
        Node cur = root;
        while (cur != null || !stack.isEmpty()) {
            while (cur != null) { stack.push(cur); cur = cur.left; }
            cur = stack.pop();
            visit.accept(cur.key);
            cur = cur.right;
        }
    }
}
```

## Pitfalls
- Hand-rolled BSTs without balancing degrade silently on sorted or nearly sorted input (timestamps, sequential ids): use `TreeMap` or sort an array and binary search ([[binary-search-and-variants]]).
- A `TreeMap` comparator that returns 0 for distinct objects (`comparing(User::getAge)`) silently drops entries: compare by a unique field last (`thenComparing(User::getId)`).
- Recursing over a degenerate tree of 100k nodes overflows the thread stack; balanced trees are safe because depth is ~40 ([[stack-vs-heap-memory]]).
- Mutating a key object inside a `TreeSet` breaks ordering without any exception; keys must be immutable ([[immutable-objects]]).
- Related: [[data-structures]] for the overview, [[hash-tables]] for when you do not need order (O(1) instead of O(log n)), [[database-indexes]] and [[postgres-indexes]] for B-trees in practice.
