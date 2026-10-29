---
title: Process
tags: [operating-systems, process, memory-layout, stack, heap]
learned: 2026-10-27
order: 3
aliases: [stack-vs-heap-memory]
---
## TL;DR
A **program** is an executable file: source code compiled and linked into machine code for one CPU architecture, stored in a format the OS can load (ELF on Linux). A **process** is that program running. The kernel has loaded it into memory, given it an id (PID) and a control block (PCB), and pointed a CPU's **program counter** at its first instruction. One program can run as many processes at once. Inside, every process has the same layout: **text** (code), **data** (globals), a **heap** that grows up, and a **stack** that grows down. Execution is the CPU repeating *fetch the instruction at PC, execute it, move PC on*. Every function call adds a **frame** to the stack, located through two registers: the **stack pointer** and the **base pointer**. The **return address** saved in the frame is how the caller resumes. The heap is managed by an allocator (`malloc`, or the JVM's TLABs) that asks the kernel for pages in bulk; what makes the stack fast is not magic but locality: its data sits next to each other, so one cache line brings several variables at once.

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

### Compiling and linking
- **Compiling** translates each source file into an **object file** (`.o`): machine code for one CPU, with holes where it calls functions defined elsewhere (`printf`, `SSL_connect`). **Linking** fills those holes and produces one executable.
- How the compiler and linker work, static vs dynamic linking, sonames and linkers like mold are in [[compilers-and-linkers]].

### Who starts a program
- `execve()` asks the **kernel** to read the ELF header, create a new address space and map the segments. If the program is dynamically linked, its header names an **interpreter**, `/lib64/ld-linux-x86-64.so.2`; the kernel maps that too and starts there. The loader maps the shared libraries (`ldd ./app` lists them) and fixes up the calls.
- The **entry point** in the ELF header is not `main` but `_start`, a few instructions from the C runtime (`crt1.o`) that set up the arguments and environment and then call `main`. When `main` returns, `exit()` runs and the process ends. The lecture simplifies this to "the header says start at main".
- Tools that understand the format can read and even rewrite it (patch code, add sections). That is how debuggers, profilers and, less nicely, some malware work; it is also why code signing exists.

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
- **A process lives in memory.** Its code, data, heap and stacks occupy pages of RAM (virtual pages mapped to physical frames, [[memory-management]]). When it exits, they are freed.
- **It is uniquely identified by an id.** The **PID** is unique among running processes on that machine (or in that container's PID namespace).
- **It has an instruction pointer / program counter.** The PC holds the address of the next instruction to execute. Strictly, every *thread* has its own PC and stack. A single-threaded process has one of each.
- **The kernel keeps a Process Control Block (PCB)** for it, `task_struct` in Linux. The PCB records the PID and parent, the state (running, sleeping…), the saved registers including PC and SP, the memory map, open files, and scheduling data. A context switch saves the CPU's registers into the PCB and loads another one's ([[process-management]]).

- **No two processes stay identical.** They start from the same code, but each one branches on its own input, time and environment (`if hour == 7 …`), so each ends up on different instructions with different values. The code is the program; the state is the process.
- **How PIDs are assigned**: Linux hands out increasing numbers up to `/proc/sys/kernel/pid_max` (4,194,304 on most 64-bit systems) and then wraps around, skipping PIDs still in use. A PID is not free the moment a process dies: it stays taken by the *zombie* until the parent collects the exit status with `wait()` ([[signals-and-process-lifecycle]]). Reuse is still possible, so "kill the process with PID 4242" can hit a new, unrelated process; Linux 5.3 added `pidfd` handles that cannot be confused this way.
- **PIDs are per namespace.** A container gets its own PID namespace: your Java app is PID 1 inside it and, say, PID 31877 on the host, and three containers can each have their own PID 700. Namespaces also isolate the network (interfaces, ports), mounts, hostname, IPC and user ids; together with cgroups they are what a container is ([[docker-fundamentals]]). Containers used to be called *jails* (FreeBSD).
- **The PC is a register, not a memory variable.** Updating a copy in memory after every instruction would cost a memory write each time, so the PC lives only in the CPU. It is written to memory only when the thread stops running on that core: on a context switch into the PCB (a save point, as in a game), on entry to the kernel onto the kernel stack.

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

Between the heap and the stack is a large gap of unmapped addresses, and the two grow towards each other. In a 64-bit process the gap is terabytes, so they never meet. A "stack overflow" means hitting the per-thread limit (8 MB by default, 1 MB for a JVM thread), not running into the heap (the stack overflow section below).

### Seeing it live: `/proc/<pid>/maps`
The kernel publishes each process's memory map as a text file. Every line is one region: address range, permissions (`r`ead, `w`rite, e`x`ecute, `p`rivate or `s`hared), offset, device, inode and the file it comes from.

```text
$ cat /proc/13503/maps            (addresses shortened)
5600a000-5600b000 r-xp ... /home/pi/high_cpu     text: machine code, 4 KiB
5601a000-5601b000 r--p ... /home/pi/high_cpu     read-only data: constants, strings
5601b000-5601c000 rw-p ... /home/pi/high_cpu     data: initialised globals
5602c000-5604d000 rw-p ... [heap]                heap: 0x21000 = 132 KiB
7f8a1000-7f9f6000 r-xp ... /usr/lib/…/libc.so.6  libc code, shared by all processes
7f9f6000-7f9fa000 rw-p ... /usr/lib/…/libc.so.6  libc's own data, private
7ffc2000-7ffe3000 rw-p ... [stack]               main thread's stack
```

- Size = end − start: `0x5600b000 − 0x5600a000 = 0x1000` = 4 KiB. The data segment holds one `int`, yet takes 4 KiB: memory is mapped in whole **pages** ([[memory-management]]).
- The text, constants and data come from the executable file; the heap and stack have no file behind them, because they are created while the process runs.
- Shared libraries appear as several lines each (code, read-only data, data). If an attacker could replace `libc.so.6` on disk, every program loading it would run their code, which is why system library directories are root-only.
- `pmap -x <pid>` prints the same with resident sizes. For a JVM, the map has hundreds of lines (the Java heap, metaspace, code cache, thread stacks, `libjvm.so`); `jcmd <pid> VM.native_memory summary` (with `-XX:NativeMemoryTracking=summary`) names them ([[jvm-memory-areas]]).

```anim
proc-maps
```

### How a process executes
- The CPU holds the running thread's state in **registers**: general-purpose ones for values (`r0`–`r3` below), the **PC**, the **instruction register (IR)** that holds the instruction being executed, the **stack pointer** and the flags ([[cpu]]).
- Execution is one loop, repeated billions of times a second:
    1. **Fetch**: read the instruction at the address in PC into the IR. This is a memory read, usually served from the L1 instruction cache.
    2. **Decode and execute**: the control unit works out what the instruction means, and the ALU or the load/store unit carries it out.
    3. **Increment PC**: move to the next instruction (+4 bytes on ARM; 1–15 bytes on x86, whose instructions vary in length). Jumps, calls and returns *replace* the PC instead.
    4. **Store**, when the instruction writes memory: the value goes to the L1 cache first and reaches RAM later.

```anim
cpu-fetch-execute
```

Real cores overlap these stages for many instructions at once ([[cpu]]), but each instruction still goes through them in this order ([[cpu]]).

- **Why memory access dominates.** Approximate costs, and the same numbers stretched so that 1 ns becomes 1 second:

| Access | Time | If 1 ns were 1 s |
|---|---|---|
| Register | < 1 ns | under a second |
| L1 cache | ~1 ns | 1 second |
| L2 cache | ~4 ns | 4 seconds |
| L3 cache (shared by cores) | ~10–20 ns | 15 seconds |
| RAM | ~100 ns | almost 2 minutes |
| NVMe SSD read | ~20–150 µs | 6 hours to 2 days |
| HDD seek | ~10 ms | 4 months |

- **Instruction fetches are usually cache hits.** A read from RAM brings a whole 64-byte cache line, which holds 16 four-byte instructions, so the next ones are already in the L1 instruction cache. Code that runs straight through is cheap; code that jumps all over the binary is not (see inlining below).
- **A quiz from the lecture**: `mov r0,#1; mov r1,#3; add r3,r0,r1` has one `add`, but the PC was also advanced three times. The lecture counts those as additions too; in hardware the PC is advanced by its own small incrementer, not the ALU, so they do not compete with your arithmetic.
- **Instruction size is not word size.** ARM64 instructions are 4 bytes even though registers are 64-bit; x86-64 instructions are 1–15 bytes. In the lecture's gdb demo the PC moved by 8 after `next` because that C line compiled to two instructions, not because a 64-bit CPU uses 8-byte instructions.
- **One core runs one thread at a time.** With **hyper-threading** (SMT), one physical core shows up as two logical CPUs: each has its own registers and PC, but they share the execution units and the L1/L2 caches. When one thread waits for memory the other can run, so it helps some workloads and hurts others.
- **The text segment is read-only** (and the stack and heap are not executable), so a bug cannot overwrite code and injected data cannot run. JIT compilers such as the JVM's need to *generate* code at runtime, so they write it into a separate mapping, the **code cache** (`-XX:ReservedCodeCacheSize`); how they get around this protection is in [[compilers-and-linkers]].
- **See it yourself** (any Linux box; on ARM the registers are `x0`…, on x86-64 `rax`…):

```bash
uname -a                       # kernel version and CPU architecture (aarch64, x86_64)
gcc -S -o test.s test.c        # stop after compiling: human-readable assembly
gcc -g -o test test.c          # full build with debug symbols (source line ↔ address)
gdb ./test
(gdb) start                    # run to the first line of main and stop
(gdb) info registers pc sp     # the program counter and stack pointer right now
(gdb) next                     # one C line; run info registers again: pc has moved
(gdb) disassemble              # the machine code around pc
```
Debug symbols are the map from machine-code addresses back to file and line, which is why `-g` binaries are much larger and why stack traces of stripped binaries show only addresses.

### The stack
- **The stack is a simple, brilliant data structure:** last in, first out. That matches function calls exactly: the function called last returns first.
- **A function has local variables**, and **each function call gets a frame**: a block of the stack holding its locals, the saved base pointer of the caller and the return address.
- **It grows from high addresses to low.** A new frame sits *below* its caller's.
- **Stack space is limited**: 8 MB for the main thread by default (`ulimit -s`), 1 MB per JVM platform thread (`-Xss`). Deep or unbounded recursion runs out of it.

- **Things are next to each other**, and that is the whole secret of its speed. Reading one local brings its 64-byte cache line, so the neighbouring locals arrive for free. It is the same reason a B+ tree's sorted leaf pages make range scans fast in a database: one read, many useful values.
- One stack per thread, reserved when the thread starts and backed by real pages only as it is touched. Below it sits an unmapped **guard page**: running past the limit hits it and faults instead of silently overwriting other memory, and the JVM turns that fault into `StackOverflowError`.
- When a function returns, its frame is not cleaned: the bytes stay until the next call overwrites them. Reading an uninitialised local in C returns that garbage, and leaked stack contents are a classic source of security bugs.
- The compiler knows each frame's size, so it allocates the whole frame with one `sub sp` at entry instead of one move per variable, and keeps the stack aligned (16 bytes on x86-64 and ARM64).

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

### Parameters, return values and registers across calls
- Arguments are passed **in registers**, not on the stack, as long as there are few enough. The rules are the platform's **calling convention** (ABI):

| | Arguments | Return value | Return address |
|---|---|---|---|
| x86-64 Linux/macOS (System V) | `rdi, rsi, rdx, rcx, r8, r9`, then the stack | `rax` | pushed on the stack by `call` |
| ARM64 | `x0`–`x7`, then the stack | `x0` | link register `x30` |

- The callee is free to use registers for its own work, so the convention also says who saves what: **caller-saved** registers may be overwritten by any call, and the caller must keep anything it needs in memory or in **callee-saved** registers, which the callee must restore before returning. That is why, in the unoptimised walkthrough below, `main` reloads `a` and `b` from the stack after `func1` returns: `func1` overwrote `r0`.
- `malloc(4)` follows the same rules: `4` goes in the first argument register and the pointer comes back in the return register.

### What a call costs, and inlining
- A call is not free: save the return address and the old frame pointer (memory writes), move SP, jump the PC somewhere else, and on return read them back. Arguments in registers may first need saving. For a small function this bookkeeping can cost more than the body.
- The jump matters too. The callee's code is elsewhere in the text segment, maybe not in the instruction cache; a program that is a deep web of small functions keeps evicting and refetching code. The lecture tells a story about MySQL 8.0 being slower than 5.7 on some workloads, with one suspected cause being many more, smaller function calls thrashing the instruction cache. Treat it as a hypothesis, but the mechanism is real.
- **Inlining** removes the call: the compiler copies the callee's body into the caller, so it becomes a few more instructions in a row. The cost is a bigger binary, which also puts pressure on the instruction cache, so compilers inline only small or hot functions.
- In Java the JIT does this at runtime with real profile data: hot methods smaller than `-XX:FreqInlineSize` (325 bytes of bytecode) are inlined, which is why getters, small helpers and lambdas usually cost nothing. A call site that sees many different implementations of an interface (*megamorphic*) cannot be inlined and stays a real call.
- **Recursion** pays this cost per level and uses a frame per level, so very deep recursion is both slow and limited by the stack size.

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
The heap is the region for memory whose size or lifetime is not known in advance: it is allocated while the program runs and stays until someone releases it.

| Property | What it means |
|---|---|
| Data that outlives a call | A request object returned from the method that built it, a cache, a connection pool. Not only "large" data: a 16-byte `malloc` is on the heap too; what decides is lifetime and run-time size |
| Lives until freed | Released explicitly (`free` in C, `delete` in C++) or, in Java, by the garbage collector once nothing references it. Returning from a function frees nothing on the heap |
| Reachable from any function, through a pointer | The block itself has no name. Code reaches it through an address held on the stack, in the data section or in another heap block. Lose every copy of the address and the block is lost |
| Grows from low addresses to high | Toward the stack, which grows down. The allocator gets pages from the kernel in bulk (`brk`, `mmap`) and hands out small pieces itself |
| API | C: `malloc` / `free`. C++: `new` / `delete`. Java: `new`, freed by the GC |

- Because the process asks for pages in bulk, the JVM's "used heap" and the kernel's "resident memory" of the process are different numbers: the JVM may hold pages it is not currently using ([[jvm-memory-areas]], [[garbage-collection]]).
- Heap memory is shared by all threads of the process. Two threads can hold references to the same object, which is both the point (shared state) and the danger (needs synchronisation).

```anim
heap-leak
```

### How malloc and free work
- **`malloc` is a library function, not a system call** (the lecture calls it one). It is part of libc and manages the heap inside the process. Only when it runs out of space does it ask the kernel for more: it moves the **program break**, the top of the heap, with `brk`/`sbrk` for small blocks, and creates a separate mapping with `mmap` for large ones (glibc's default threshold is 128 KiB). Most `malloc` calls never enter the kernel.
- **`free(p)` knows the size because of a header.** The allocator stores a small header (in glibc, 8–16 bytes holding the chunk size and flags) just before the address it returns. `free` steps back from `p`, reads it and puts the chunk on a free list. That is overhead on every allocation: a 4-byte `malloc` takes a 32-byte chunk on 64-bit glibc.
- `free` gives memory back to the **allocator**, not necessarily to the kernel. A chunk in the middle of the heap cannot lower the program break, so the process's RSS often stays high after a burst of allocations. Only `mmap`-ed blocks are returned at once.
- Many tiny allocations in a loop are the worst pattern: each pays the allocator's bookkeeping and header, and the blocks scatter. Programs that know their workload allocate in bulk: **memcached** carves memory into slabs of fixed-size chunks, servers use arenas and pools, and in Java Netty pools its `ByteBuf`s for the same reason. The lecture blames OOP constructors that allocate per object for encouraging the bad pattern; in Java the cheap TLAB allocation softens it, but large buffers are still worth reusing.
- glibc gives threads separate **arenas** to avoid a global lock. A JVM with hundreds of threads can create many arenas, each holding freed native memory, so a container's RSS grows far beyond `-Xmx`; `MALLOC_ARENA_MAX=2` or jemalloc is the common fix.

### Pointers
- A **pointer** is a variable whose value is an address. Its **type** says how many bytes to read there: `int *p` reads 4 bytes, `char *c` reads 1. Without a type the CPU would not know where the value ends.
- The pointer itself has a size too: 4 bytes in a 32-bit process, 8 in a 64-bit one. A pointer can live on the stack, in the data section or on the heap, and point to any of them. Java references are pointers you cannot do arithmetic on (compressed to 4 bytes when the heap is under 32 GB).

### Memory leaks
- In C a leak is a block that is still allocated but whose address no one holds any more. The classic case is in the animation: the only pointer was a local variable, and the function returned without `free`. Nothing can release that memory until the process exits.
- A leak in a short command-line program is harmless, because the kernel takes back all memory when the process ends. In a server that runs for weeks, a few kilobytes per request add up to an out-of-memory kill.
- The opposite mistakes are just as bad: using a block after `free` (*use-after-free*, through a *dangling pointer*) and calling `free` twice (*double free*). Both corrupt the allocator's bookkeeping and are a classic source of security holes.
- Java cannot leak this way: the GC frees every object that is unreachable. A Java leak is an object that is still **reachable** but no longer needed: a `static Map` used as a cache with no eviction, listeners that are registered and never removed, a `ThreadLocal` left set in a pooled thread, a growing queue no one drains.
- The symptom of a Java leak: after each full GC, the old generation stays a little higher, until `OutOfMemoryError: Java heap space`. Diagnose with a heap dump (`-XX:+HeapDumpOnOutOfMemoryError`, then the dominator tree in Eclipse MAT) to see which object keeps the rest alive.

- A dangling pointer or double free is worse than a leak: a leak only wastes memory, while these crash the process or let an attacker corrupt it. If a crafted HTTP request can trigger a double free in a C server, sending it repeatedly crashes every worker: a denial of service. Rust rules these bugs out at compile time; garbage-collected languages make them impossible.
- **The JVM does not count references.** The lecture says garbage collectors use reference counting; CPython does (and pays a memory write on every reference change, see [[operating-system-introduction]]), but HotSpot's collectors **trace**: they start from roots (stacks, static fields) and mark everything reachable. Reading an object never writes to it, and cycles are collected naturally.

### Stack vs heap: performance
| | Stack | Heap |
|---|---|---|
| Allocate and free | Move `sp`: one instruction, and everything is freed automatically on return | The allocator searches its free lists, may take a lock, sometimes makes a system call; memory must be freed by hand or by the GC |
| Locality | Frames are contiguous and recently used, so they sit in the L1 cache | After many allocations and frees, related blocks end up scattered ("heap is random"), so walking them causes cache misses ([[cpu-caches-and-cache-lines]]) |
| Size | Small and fixed per thread: 8 MB for the main thread on Linux (`ulimit -s`), 512 KB to 1 MB for a Java thread (`-Xss`). Overflow means `StackOverflowError` | Limited by RAM, the address space, and in Java by `-Xmx` |
| Fragmentation | None: the stack is always one contiguous block | Holes between live blocks, like the freed 100 bytes in the animation, that may be too small to reuse |
| Owner | One thread: no synchronisation needed | The whole process: shared objects need synchronisation |
| Lifetime | Until the function returns | Until freed, or until unreachable in Java |
| Failure | `StackOverflowError` / `SIGSEGV` | `OutOfMemoryError`, `malloc` returns `NULL`, or the OOM killer |

- In Java, allocation is almost as cheap as on the stack: each thread bumps a pointer inside its own buffer in the young generation (a TLAB). The cost moves to garbage collection, which is why "allocate less" is still a performance rule ([[garbage-collection]]).

- **Layout beats instruction count.** Whatever the region, fields that are used together should sit in the same cache line. In 2024 (Linux 6.8) Google engineers reordered the fields of the kernel's core networking structures (`struct sock`, `tcp_sock`, `net_device`) so that the fields read on the hot path share cache lines. Nothing else changed, and TCP throughput with many concurrent connections rose by up to about 40% on some machines. Java does not let you order fields (the JVM lays objects out itself), but the same idea explains why an `int[]` is much faster to scan than a `List<Integer>` of scattered boxed objects ([[cpu-caches-and-cache-lines]]).
- **Escape analysis** moves heap allocations to the stack when they can: Go's compiler puts a value on the stack if no pointer to it outlives the function. HotSpot's JIT does a similar thing (scalar replacement): a non-escaping object is split into locals and never allocated at all. You cannot rely on it, but it is why small temporary objects are often free.
- Native memory in a JVM process is "heap" in the OS sense too: metaspace, the JIT code cache, direct `ByteBuffer`s and thread stacks all count against a container's limit although they are outside `-Xmx`.

### Why stack overflow happens
- Unbounded or very deep recursion: recursive walks of a degenerate (linked-list-shaped) tree, recursive JSON mapping of a self-referencing entity, a `toString()`/`hashCode()` cycle between two JPA entities ([[jpa-entities-and-relationships]]), a Spring bean that calls itself through a proxy ([[spring-aop]]).
- Large frames: big local arrays in C. In Java locals are references, so frames are small, but deep chains of inlined calls and very long stream pipelines inflate them.
- Thread count × stack size: 5,000 platform threads × 1 MiB = 5 GiB of reserved address space. Not an overflow, but a reason to run out of memory or hit `ulimit -u`. Virtual threads keep their frames on the heap and grow them on demand ([[virtual-threads]]).
- Fix: iterate with an explicit `Deque` instead of recursing (see the second example), cap the depth, or give a larger stack to one dedicated thread (`new Thread(group, task, name, stackSize)`) rather than raising `-Xss` for all ([[recursion-and-backtracking]]).

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

Avoiding a stack overflow with an explicit stack on the heap:

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
- **"exec format error"** is a program built for another CPU. The typical case is a `linux/amd64` Docker image on an ARM64 machine (Apple Silicon, AWS Graviton). Build multi-arch images (`docker buildx --platform linux/amd64,linux/arm64`) or run under emulation, which is much slower.
- A Java object is never on the stack, only its reference is. "Stack allocation" in Java exists only as a JIT optimisation (escape analysis) that you cannot rely on.
- Thread stacks are not in the "stack" region of the layout. Only the main thread's is; every other thread's stack is a separate memory-mapped area, which is why thousands of platform threads cost real memory ([[virtual-threads]]).
- Optimised builds often omit the frame pointer (`-fomit-frame-pointer`) to free a register. Profilers and `perf` then struggle to walk the stack. For JVM flame graphs use `-XX:+PreserveFramePointer`.
- In C, writing past a local array can overwrite the saved return address. That is the classic *stack buffer overflow* exploit, and the reason stack canaries and non-executable stacks exist.
- `ps` shows virtual size (VSZ) and resident size (RSS). VSZ counts every mapped page including the unused heap reserve and shared libraries; RSS is what is actually in RAM. Compare RSS, never VSZ, with a container's memory limit ([[docker-containers-and-resources]]).
- `fork` copies the layout lazily (copy-on-write), so a process with a 4 GB heap can fork quickly, but the first write to each page after the fork costs a page fault ([[memory-management]]).
- Catching `StackOverflowError` as if it were an ordinary exception: it is an `Error`, and the thread is left in an unknown state. Fix the recursion.
- Raising `-Xss` globally to "fix" an overflow: every thread now reserves more memory, and the real bug (usually a cycle) stays.
- A container OOM-killed while the Java heap is well under `-Xmx`: the culprit is native memory (direct buffers from Netty or the Kafka client, metaspace, thread stacks, malloc arenas), which the Java heap numbers do not show.
- In C, returning the address of a local, or `alloca`/variable-length arrays with a user-controlled size: both corrupt the stack, which Java prevents by design.
- "Missing library" errors after copying a binary to another machine (`error while loading shared libraries`): ship it in a container, link it statically, or install the matching library version.
- Related: [[jvm-memory-areas]] for how the JVM divides its part of this layout, [[process-management]] for threads, the PCB and context switches, [[memory-management]] for pages and virtual memory, [[compilation-interpretation-jit]] for how bytecode becomes machine code, [[compilers-and-linkers]] for how the executable is built.
