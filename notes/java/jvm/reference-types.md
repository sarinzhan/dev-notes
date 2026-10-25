---
title: Strong, soft, weak and phantom references
tags: [gc, references, memory, cache]
learned: 2026-10-22
---
## TL;DR
`java.lang.ref` defines four strengths of reference. The strength decides when the garbage collector may reclaim the object: never while strongly reachable, only under memory pressure for soft, at the next GC for weak, and after finalization (with a queue notification) for phantom.

## Key points
- **Strong**: the ordinary `Object o = new Object()`. While any strong reference exists the object is never collected.
- **Soft** (`SoftReference`): collected only when the JVM is about to run out of memory, and last of all. Meant for memory-sensitive caches: the object stays as long as there is room.
- **Weak** (`WeakReference`): collected at the next GC cycle as soon as no strong references remain; the reference does not keep the object alive at all. After collection `get()` returns `null`. Used by `WeakHashMap` (keys), listeners, canonicalizing maps.
- **Phantom** (`PhantomReference`): `get()` always returns `null`; the object is unreachable to the program. When the object becomes phantom-reachable the reference is put on a `ReferenceQueue`, which lets you run cleanup *after* the object is gone (the modern replacement for `finalize()`, used by `Cleaner`).
- All non-strong references can be registered with a `ReferenceQueue` to be notified when the referent is collected.

## Example
```java
Map<Key, WeakReference<Image>> cache = new HashMap<>();

Image load(Key key) {
    WeakReference<Image> ref = cache.get(key);
    Image img = ref == null ? null : ref.get();
    if (img == null) {
        img = decode(key);
        cache.put(key, new WeakReference<>(img));
    }
    return img;
}
```

## Pitfalls
- A `SoftReference` cache has no eviction policy you control; in practice Caffeine with explicit sizing behaves better.
- `WeakHashMap` keys are weak but values are strong: a value that references its key keeps the entry alive forever.
- `finalize()` is deprecated; use `Cleaner` with a phantom reference instead.
