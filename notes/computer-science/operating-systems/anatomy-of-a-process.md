---
title: Anatomy of a process
tags: [operating-systems, process, memory-layout, stack, heap]
learned: 2026-10-27
---
## TL;DR
A **program** is a file on disk: machine code plus a description of how to lay it out in memory. A **process** is that program loaded into its own virtual address space and running: a copy of the code (**text**), the global variables (**data** and **bss**), a growing region for objects allocated at runtime (**heap**), one **stack** per thread for function calls, and a set of CPU registers that say which instruction runs next. Execution is nothing more than the CPU repeating "fetch the instruction at the program counter, run it, move on", with every function call pushing a frame on the stack and every return popping it.

## Key points
### Program
- A program is an executable file (ELF on Linux, a `.class`/`.jar` for the JVM, which is itself an ELF program). It contains sections: the machine code, initialised constants, the size of the uninitialised globals, and a header saying where each section should land in memory and where execution starts (the entry point).
- A program does nothing by itself. It becomes a process when the kernel reads the header, builds an address space, maps the sections into it, and sets the CPU's program counter to the entry point ([[processes-and-threads]]).
- One program can be many processes: ten `java` commands are ten processes sharing the same code pages in RAM (read-only, so the kernel maps them once) but each with its own data, heap and stacks.

### Process memory layout
From low addresses to high, in a typical Linux process:

| Region | Holds | Lifetime | Grows |
|---|---|---|---|
| Text | Machine code of the program and shared libraries | Whole process | Fixed, read-only, shared between processes |
| Data | Globals and statics with an initial value (`static int retries = 3;`) | Whole process | Fixed |
| BSS | Globals and statics that start as zero; the file stores only their size | Whole process | Fixed, zero-filled on load |
| Heap | Everything allocated at runtime: `malloc`, `new`, the JVM heap | Until freed (or collected) | Upwards, on request (`brk`, `mmap`) |
| Memory-mapped area | Shared libraries, mapped files, large allocations, thread stacks | Varies | Both ways |
| Stack | Call frames of the main thread: parameters, locals, return addresses | One function call | Downwards, automatically |

Between the heap and the stack there is a large gap of unmapped addresses; the two grow towards each other. In a 64-bit process the gap is terabytes, so they never meet; a "stack overflow" is a per-thread limit (8 MB by default, 1 MB for a JVM thread), not the heap being in the way ([[stack-vs-heap-memory]]).

### Process execution
- The CPU holds the process's state in registers: the **program counter** (the address of the next instruction), the **stack pointer** (the top of the current stack), general-purpose registers for arithmetic and arguments, and flags.
- Execution is a loop: fetch the instruction at the program counter, decode it, execute it, advance the counter ([[how-cpu-executes-code]]). Jumps and calls change the counter instead of advancing it.
- When the kernel switches to another thread it saves these registers into the thread's kernel structure and loads the other thread's. That save and restore is the context switch ([[processes-and-threads]]).
- The JVM adds a layer: the interpreter and the JIT compiler turn bytecode into machine code, but once compiled, a Java method runs through exactly this loop, with the same stack and registers ([[compilation-interpretation-jit]]).

### The stack
- A region per thread, used as LIFO. The stack pointer marks the top; "pushing" means moving the pointer down and writing, "popping" means moving it back up. Allocation and release cost one instruction each, which is why locals are cheap.
- Each function call creates a **frame**: the return address (where to continue after the call), the caller's saved registers, the function's parameters and local variables.
- The frame is discarded on return, so anything stored in it is gone. A pointer to a local returned from a function is the classic C bug; Java prevents it by keeping objects on the heap and only references on the stack.

### Process execution with the stack
A call sequence `main → handle → parse`, traced:

```text
                 stack (grows down)                   program counter
main() starts    [ main frame: locals ]               in main
main calls       push return addr, args               jump to handle
handle() runs    [ main ][ handle frame ]             in handle
handle calls     push return addr, args               jump to parse
parse() runs     [ main ][ handle ][ parse frame ]    in parse
parse returns    pop parse frame, load return addr    back in handle, after the call
handle returns   pop handle frame                     back in main, after the call
```

A stack trace is this picture read top-down: the exception was created in `parse`, which was called from `handle`, which was called from `main`. Recursion with no base case keeps pushing frames until the per-thread limit, and the kernel delivers a fault that the JVM reports as `StackOverflowError`.

### Data section
- Globals and `static` fields live here for the whole life of the process; they are not freed and not per-thread. In Java, static fields of a loaded class live in the JVM's metaspace and the objects they reference on the heap; the idea is the same: one copy, visible to every thread.
- Because they are shared, they are the first suspect in concurrency bugs: a `static` counter incremented without synchronisation is a data race ([[race-conditions-and-critical-sections]]).
- The split between data (initialised) and BSS (zero) exists so the executable file does not store megabytes of zeros.

### Heap
- The heap holds objects whose lifetime is not tied to a call: a request object that outlives the method that created it, a cache, a connection pool. Allocation goes through an allocator that finds free space; release is explicit (`free`) or automatic (garbage collection).
- The process asks the kernel for heap pages in bulk (`brk`, `mmap`) and then hands out small pieces itself. That is why the JVM's "used heap" and the kernel's "resident memory" of the process are different numbers: the JVM may hold pages it is not currently using ([[jvm-memory-areas]], [[garbage-collection]]).
- Heap memory is shared by all threads of the process. Two threads can hold references to the same object, which is both the point (shared state) and the danger (needs synchronisation).

## Example
A tiny C-like program and where each piece lands:

```c
int max_retries = 3;          // data: initialised global
int request_count;            // bss: zero-initialised global
static char buffer[4096];     // bss: static array, zero-filled

int parse(const char *s) {    // text: machine code
    int n = 0;                // stack: local in parse's frame
    while (*s) n = n * 10 + (*s++ - '0');
    return n;                 // frame popped; n is gone
}

int main(void) {
    char *body = malloc(1024);         // heap: lives until free()
    int id = parse("42");              // call: push frame, run, pop
    request_count++;                   // data/bss: same address for the whole process
    free(body);
    return 0;
}
```

The same program in Java moves `body` and the string to the GC-managed heap and `max_retries`/`request_count` into static fields, but `n`, `id` and the reference `body` are still on the stack, and `parse` still gets a frame per call.

## Pitfalls
- A Java object is never on the stack, only its reference is. "Stack allocation" in Java exists only as a JIT optimisation (escape analysis) that you cannot rely on.
- Thread stacks are not in the "stack" region of the layout. Only the main thread's is; every other thread's stack is a separate memory-mapped area, which is why thousands of platform threads cost real memory ([[virtual-threads]]).
- `ps` shows virtual size (VSZ) and resident size (RSS). VSZ counts every mapped page including the unused heap reserve and shared libraries; RSS is what is actually in RAM. Compare RSS, never VSZ, with a container's memory limit ([[docker-containers-and-resources]]).
- `fork` copies the layout lazily (copy-on-write), so a process with a 4 GB heap can fork quickly, but the first write to each page after the fork costs a page fault ([[virtual-memory-and-paging]]).
- Related: [[stack-vs-heap-memory]] for the detailed comparison, [[jvm-memory-areas]] for how the JVM divides its part of this layout.
