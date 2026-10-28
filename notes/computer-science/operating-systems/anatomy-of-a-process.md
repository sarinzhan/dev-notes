---
title: Process
tags: [operating-systems, process, memory-layout, stack, heap]
learned: 2026-10-27
order: 3
---
## TL;DR
A **program** is an executable file: source code compiled and linked into machine code for one CPU architecture, stored in a format the OS can load (ELF on Linux). A **process** is that program running. The kernel has loaded it into memory, given it an id (PID) and a control block (PCB), and pointed a CPU's **program counter** at its first instruction. One program can run as many processes at once. Inside, every process has the same layout: **text** (code), **data** (globals), a **heap** that grows up, and a **stack** that grows down. Execution is the CPU repeating *fetch the instruction at PC, execute it, move PC on*. Every function call adds a **frame** to the stack, located through two registers: the **stack pointer** and the **base pointer**. The **return address** saved in the frame is how the caller resumes.

## Key points
### Program
- **Compiled and linked for one CPU.** A compiler turns source into the instruction set of a specific architecture (x86-64, ARM64). The linker joins your object files and libraries into one executable. The result **runs only on that CPU architecture**: an x86-64 binary does nothing on an ARM64 machine without emulation.
- **Also tied to one OS.** The file format and the system calls differ: ELF on Linux, PE on Windows, Mach-O on macOS. The same x86-64 CPU cannot run a Windows `.exe` natively on Linux.
- **At rest it follows an executable file format.** For ELF:

```anim
program-to-process
```

| Part of the ELF file | What it holds | Who reads it |
|---|---|---|
| ELF header | Magic bytes `7f 45 4c 46`, the target CPU, the entry point address | Kernel, tools |
| Program header table | **Segments**: which byte ranges to map into memory, where, and with what permissions (R-X for code, RW- for data) | Kernel loader at `exec` |
| `.text` | Machine code | Lands in the code segment (R-X) |
| `.rodata` | Constants and string literals | Read-only segment |
| `.data` | Globals with an initial value | Read-write segment |
| `.bss` | Size of the zero-initialised globals (no bytes in the file) | Loader allocates zeroed pages |
| Section header table | **Sections**: names, types and offsets for the linker, the debugger, `objdump` | Build tools, not needed to run |

The same bytes are described twice. **Sections** are the linker's fine-grained view; **segments** are the loader's coarse view of what to map. `readelf -S` shows the first and `readelf -l` the second.
- Java is the deliberate exception: `javac` produces CPU-independent bytecode in `.class` files. The `java` command itself is an ELF program compiled for your CPU, and its JIT turns hot bytecode into machine code for the current CPU at runtime ([[compilation-interpretation-jit]]).

### Producing machine code
Each line of source becomes a few instructions. Here is a C function compiled to simplified ARM-style assembly, the same shape as real output with fewer details:

```text
int main() {                       mov r0, #1         ; a = 1, kept in register r0
    int a = 1;                     mov r1, #3         ; b = 3, kept in register r1
    int b = 3;            compile  add r3, r0, r1     ; c = a + b, computed by the ALU
    int c = a + b;        ──────▶  str r3, [sp, #4]   ; store c to its slot on the stack
    printf("%d", c);               ...                ; then call printf
}
```

Variables that the compiler can keep in **registers** never touch memory. Only `str` (store) and `ldr` (load) move data between registers and RAM. On x86-64 the same function uses `mov`, `add` and `[rbp-4]`-style addresses, but the idea is identical.

### Process
- **When a program is run, we get a process.** `execve()` asks the kernel to read the ELF headers, create a new address space, map the segments, set up a stack, and jump to the entry point ([[signals-and-process-lifecycle]]).
- **A process lives in memory.** Its code, data, heap and stacks occupy pages of RAM (virtual pages mapped to physical frames, [[virtual-memory-and-paging]]). When it exits, they are freed.
- **It is uniquely identified by an id.** The **PID** is unique among running processes on that machine (or in that container's PID namespace).
- **It has an instruction pointer / program counter.** The PC holds the address of the next instruction to execute. Strictly, every *thread* has its own PC and stack. A single-threaded process has one of each.
- **The kernel keeps a Process Control Block (PCB)** for it, `task_struct` in Linux. The PCB records the PID and parent, the state (running, sleeping…), the saved registers including PC and SP, the memory map, open files, and scheduling data. A context switch saves the CPU's registers into the PCB and loads another one's ([[processes-and-threads]]).

### Program vs process
One program file can be many processes at once. Each one has its own PID, its own PC (each is at a different point in the code) and its own data, heap and stack. The read-only code pages are shared: the kernel maps the same physical pages of `postgres` into every copy.

```text
                      /usr/lib/postgresql/17/bin/postgres      ← one program (ELF file)
            ┌──────────────────────┼──────────────────────┐
   postgres pid 2231      postgres pid 2240      postgres pid 2241   ← three processes
   PC 0x5583…a10          PC 0x5583…f48          PC 0x5583…3c0         (each at its own place)
   own heap + stack       own heap + stack       own heap + stack
```

PostgreSQL really works this way: it forks one backend process per client connection, which is why `ps` shows many `postgres` lines and why connection pooling matters ([[hikaricp-pool-sizing]]).

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

```anim
process-memory-layout
```

Between the heap and the stack is a large gap of unmapped addresses, and the two grow towards each other. In a 64-bit process the gap is terabytes, so they never meet. A "stack overflow" means hitting the per-thread limit (8 MB by default, 1 MB for a JVM thread), not running into the heap ([[stack-vs-heap-memory]]).

### How a process executes
- The CPU holds the running thread's state in **registers**: general-purpose ones for values (`r0`–`r3` below), the **PC**, the **instruction register (IR)** that holds the instruction being executed, the **stack pointer** and the flags ([[cpu-components-and-architecture]]).
- Execution is one loop, repeated billions of times a second:
    1. **Fetch**: read the instruction at the address in PC into the IR. This is a memory read, usually served from the L1 instruction cache.
    2. **Decode and execute**: the control unit works out what the instruction means, and the ALU or the load/store unit carries it out.
    3. **Increment PC**: move to the next instruction (+4 bytes on ARM; 1–15 bytes on x86, whose instructions vary in length). Jumps, calls and returns *replace* the PC instead.
    4. **Store**, when the instruction writes memory: the value goes to the L1 cache first and reaches RAM later.

```anim
cpu-fetch-execute
```

Real cores overlap these stages for many instructions at once ([[instruction-lifecycle-and-pipelining]]), but each instruction still goes through them in this order ([[how-cpu-executes-code]]).

### The stack
- **The stack is a simple, brilliant data structure:** last in, first out. That matches function calls exactly: the function called last returns first.
- **A function has local variables**, and **each function call gets a frame**: a block of the stack holding its locals, the saved base pointer of the caller and the return address.
- **It grows from high addresses to low.** A new frame sits *below* its caller's.
- **Stack space is limited**: 8 MB for the main thread by default (`ulimit -s`), 1 MB per JVM platform thread (`-Xss`). Deep or unbounded recursion runs out of it.

### Stack pointer and base pointer
- **The stack pointer (SP)** is a CPU register (`rsp` on x86-64, `sp` on ARM64) that **points at the end** of the stack: the lowest address in use.
- **Allocating and deallocating stack memory is moving SP**: `sp = sp - 12` reserves 12 bytes, `sp = sp + 12` releases them. Nothing is cleared or freed; the old bytes are simply overwritten later. That is why stack allocation costs one instruction.
- **SP changes all the time**, during pushes, calls and argument setup, so it makes a poor reference point for finding a variable. A function needs a **fixed reference**.
- **The base pointer (BP)**, also called the **frame pointer** (`rbp` on x86-64, `x29` on ARM64), stays put for the whole call and marks the top of the current frame. Locals are addressed relative to it: `a` is `[bp-4]`, `b` is `[bp-8]`, however SP moves.

### A function call, step by step
When `main` calls `func1`, on x86-64:

| Step | Instruction | What happens to the stack |
|---|---|---|
| 1. Call | `call func1` | The CPU pushes the **return address** (the address of the instruction after the call in `main`) and sets PC to `func1` |
| 2. Save caller's frame | `push rbp` | The **previous BP** (main's) is saved, so main's frame can be found again |
| 3. New frame | `mov rbp, rsp` | BP now marks the top of func1's frame |
| 4. Locals | `sub rsp, 16` | SP moves down to make room for `x` and `y` |
| 5. Run | … | `x` and `y` are read and written as `[rbp-4]`, `[rbp-8]` |
| 6. Free locals | `mov rsp, rbp` | **Deallocate**: SP jumps back up; `x` and `y` are gone (their bytes stay until overwritten) |
| 7. Restore caller's frame | `pop rbp` | BP is main's again |
| 8. Return | `ret` | The **return address is popped into PC**: main continues right after the call |

Steps 2–4 are the function's *prologue*, and steps 6–8 its *epilogue*. Every compiled function starts and ends this way, with small variations.

```anim
call-stack-frames
```

- **Why the return address matters**: main still has work to do after the call, but once PC moves to `func1` the CPU has lost its place in main. The saved return address *becomes the PC* again on return.
- **x86-64 vs ARM64**: on x86-64, `call` stores the return address **on the stack**. On ARM64, `bl func1` stores it in the **link register** (`x30`) instead, which is faster for leaf functions that call nothing else. A function that does call others first saves `x29` (frame pointer) and `x30` (return address) on its stack frame (`stp x29, x30, [sp, #-16]!`), because the next `bl` would overwrite the link register.
- In both conventions the saved return address sits *above* the saved frame pointer, at the higher address.
- A stack trace is this chain of frames read top-down: the exception was thrown in the newest frame, called from the one above it, and so on up to `main`.

### Putting it together: a whole program running
Everything above at once: a small C program (`main` sets `a` and `b`, calls `func1`, which computes `z = z + 1`, then `main` computes `c = a + b`), compiled to simplified ARM-style instructions and executed one instruction per step. Watch three things change together: **pc** walks the machine code and jumps on `bl` and `ret`; registers (**r0**, **r1**, **r3**) hold values only while they are being computed; and every `str` / `ldr` is a trip between the CPU and the stack in RAM. Addresses are decimal and small for readability; each instruction is 4 bytes.

```anim
program-execution
```

- `bl func1` stores the return address in the link register `lr` instead of pushing it. A function that calls nothing (a *leaf*, like `func1`) can leave it there; `main` saved its own `lr` in the prologue because `bl` overwrites it.
- Unoptimised code (`-O0`) stores every variable in memory and reloads it each time, as here. With `-O2` the whole program shrinks to almost nothing, since the results are never used.

### Data section
- Globals and `static` fields live here for the whole life of the process; they are not freed and not per-thread. In Java, static fields of a loaded class live in the JVM's metaspace and the objects they reference on the heap; the idea is the same: one copy, visible to every thread.
- Because they are shared, they are the first suspect in concurrency bugs: a `static` counter incremented without synchronisation is a data race ([[race-conditions-and-critical-sections]]).
- The split between data (initialised) and BSS (zero) exists so the executable file does not store megabytes of zeros.

### Heap
- The heap holds objects whose lifetime is not tied to a call: a request object that outlives the method that created it, a cache, a connection pool. Allocation goes through an allocator that finds free space; release is explicit (`free`) or automatic (garbage collection).
- The process asks the kernel for heap pages in bulk (`brk`, `mmap`) and then hands out small pieces itself. That is why the JVM's "used heap" and the kernel's "resident memory" of the process are different numbers: the JVM may hold pages it is not currently using ([[jvm-memory-areas]], [[garbage-collection]]).
- Heap memory is shared by all threads of the process. Two threads can hold references to the same object, which is both the point (shared state) and the danger (needs synchronisation).

## Example
From source to three running processes, and where each piece of a program lands:

```c
int max_retries = 3;          // .data → data segment: initialised global
int request_count;            // .bss  → zero-filled page: zero-initialised global
static const char *name = "api";  // the string literal lives in .rodata

int parse(const char *s) {    // .text → code segment (read-only, shared)
    int n = 0;                // stack: local in parse's frame, [bp-4]
    while (*s) n = n * 10 + (*s++ - '0');
    return n;                 // epilogue: frame freed, return address → PC
}

int main(void) {
    char *body = malloc(1024);         // heap: lives until free()
    int id = parse("42");              // call: push return address, new frame
    request_count++;                   // data: one address for the whole process
    free(body);
    return 0;
}

/*
  $ gcc -O0 -o api api.c          compile + link for this CPU (x86-64)
  $ file api                      → ELF 64-bit LSB pie executable, x86-64
  $ readelf -l api | grep LOAD    → the R / R E / RW segments the loader maps
  $ ./api & ./api & ./api &       one program, three processes
  $ pgrep -a api                  → three PIDs, same file
*/
```

The same program in Java moves `body` and the string to the GC-managed heap and `max_retries`/`request_count` into static fields, but `n`, `id` and the reference `body` are still on the stack, and `parse` still gets a frame per call.

## Pitfalls
- **"exec format error"** is a program built for another CPU. The typical case is a `linux/amd64` Docker image on an ARM64 machine (Apple Silicon, AWS Graviton). Build multi-arch images (`docker buildx --platform linux/amd64,linux/arm64`) or run under emulation, which is much slower.
- A Java object is never on the stack, only its reference is. "Stack allocation" in Java exists only as a JIT optimisation (escape analysis) that you cannot rely on.
- Thread stacks are not in the "stack" region of the layout. Only the main thread's is; every other thread's stack is a separate memory-mapped area, which is why thousands of platform threads cost real memory ([[virtual-threads]]).
- Optimised builds often omit the frame pointer (`-fomit-frame-pointer`) to free a register. Profilers and `perf` then struggle to walk the stack. For JVM flame graphs use `-XX:+PreserveFramePointer`.
- In C, writing past a local array can overwrite the saved return address. That is the classic *stack buffer overflow* exploit, and the reason stack canaries and non-executable stacks exist.
- `ps` shows virtual size (VSZ) and resident size (RSS). VSZ counts every mapped page including the unused heap reserve and shared libraries; RSS is what is actually in RAM. Compare RSS, never VSZ, with a container's memory limit ([[docker-containers-and-resources]]).
- `fork` copies the layout lazily (copy-on-write), so a process with a 4 GB heap can fork quickly, but the first write to each page after the fork costs a page fault ([[virtual-memory-and-paging]]).
- Related: [[stack-vs-heap-memory]] for the detailed comparison, [[jvm-memory-areas]] for how the JVM divides its part of this layout, [[processes-and-threads]] for threads, the PCB and context switches.
