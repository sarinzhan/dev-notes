---
title: Compilers and linkers
tags: [operating-systems, compilers, linkers, elf, jit, runtimes]
learned: 2026-10-29
order: 11
---
## TL;DR
The kernel runs only **machine code** for the CPU it is on, packed in an **executable format** it understands (ELF on Linux, PE on Windows, Mach-O on macOS). Two tools produce that file. The **compiler** turns each source file into an **object file**: machine code with addresses starting at zero, a **symbol table** (what this file defines, what it needs) and **relocations** (the holes to patch). The **linker** joins all object files and libraries: it resolves every symbol, lays the sections out at final addresses, patches the holes and writes the headers the kernel needs (segments, entry point, the dynamic loader). Libraries are either copied in (**static**) or recorded by name and found by `ld.so` at start (**dynamic**). Code built this way is tied to one CPU and one OS. **Interpreted** languages trade speed for portability: you ship source or bytecode, and a **runtime** compiled for each platform executes it. A **JIT** wins most of the speed back by compiling hot code at runtime into executable memory, and a **garbage collector** in the same runtime frees memory for you at the cost of headers, barriers and pauses.

## Key points
### Machine code belongs to one CPU and one OS
- Every CPU family has its own **instruction set architecture (ISA)**: the list of instructions and how each is encoded in bytes. The same addition is different bytes on each:

| | x86-64 (Intel, AMD) | ARM64 (Apple M, AWS Graviton, phones) |
|---|---|---|
| Style | **CISC**: many complex instructions, can use memory operands directly | **RISC**: simpler instructions, only loads and stores touch memory |
| Instruction length | 1 to 15 bytes | always 4 bytes |
| `x = a + b` (registers) | `add rax, rbx` = `48 01 d8` | `add x3, x0, x1` = `8b 01 00 03` |
| `write` system call | number 1 in `rax`, then `syscall` | number 64 in `x8`, then `svc #0` |

- **RISC vs CISC** is blurry today: x86 chips decode their instructions into RISC-like micro-operations internally, and ARM64 has grown hundreds of instructions. What matters is that the encodings differ, so an x86-64 binary is noise to an ARM64 CPU.
- **Bitness** works one way. A 64-bit binary cannot run on a 32-bit CPU (it uses registers and addresses that do not exist), while x86-64 CPUs still run 32-bit x86 code in a compatibility mode. Apple's ARM chips dropped 32-bit entirely.
- **The OS matters as much as the CPU.** A Linux x86-64 binary and a Windows x86-64 binary use the same instructions but different file formats, different system call numbers and different libraries. That is why the lecture's C web server, built on `socket`, `accept` and `read`, does not even compile on Windows without changes: Windows has Winsock instead of POSIX sockets.
- When the CPU differs, you can still **translate**: Apple's Rosetta 2 translates x86-64 apps to ARM64 ahead of time, and QEMU user mode translates on the fly, which is how `docker buildx` builds an `amd64` image on an ARM laptop (slowly).

### Assembly
- Assembly is machine code written as text: one line, one instruction (`mov`, `add`, `str`). The **assembler** (`as`) only translates mnemonics to bytes and names to addresses; it does not optimise or restructure.
- It is still CPU-specific (x86 and ARM assembly are different languages) and sometimes model-specific: AVX-512 instructions exist only on some x86 chips, so code using them crashes with "illegal instruction" elsewhere. Compilers can pick instructions per target (`-march=native`), and the JVM's JIT does it automatically at runtime.
- The slide's `str r3, #0xffeeddcc` is pseudo-assembly: real ARM cannot store to an address written in the instruction. The address goes into a register first (`ldr r4, =0xffeeddcc`, then `str r3, [r4]`), because a 4-byte instruction has no room for a 32-bit address plus the opcode.
- One line of C becomes a few instructions, and one line of Python becomes hundreds once the interpreter's work is counted. [[anatomy-of-a-process]] walks a small C program through its instructions, and [[cpu]] shows what the CPU does with each one.

### From source to executable: the toolchain
`gcc` and `clang` are **drivers**: they run a chain of separate programs, and `gcc -v` prints each command. You can stop after any stage:

| Stage | Program | Input → output | Stop here with |
|---|---|---|---|
| Preprocess | `cpp` | `main.c` → C with `#include`s pasted in and macros expanded | `gcc -E` |
| Compile | `cc1` (GCC), clang itself | → assembly for one CPU | `gcc -S` |
| Assemble | `as` | → object file `main.o` | `gcc -c` |
| Link | `ld` (or gold, lld, mold) | all `.o` + libraries → executable `app` | default |

- Inside, a compiler is a **front end** (parse the language, check types) producing an **intermediate representation** (GCC's GIMPLE, LLVM IR), an **optimiser** working on that IR, and a **back end** per CPU. That split is why the lecture's list has `rustc` next to `clang`: both are front ends of LLVM and share its optimiser and its x86, ARM and RISC-V back ends. Swift and Zig use LLVM too.
- The same split makes **cross-compiling** easy: `GOOS=linux GOARCH=arm64 go build` on a Mac, `clang --target=aarch64-linux-gnu`, or a Gradle build producing a GraalVM native image for Linux in a Linux container.
- Each preprocessed `.c` file is a **translation unit** and becomes exactly one `.o` (the slide's "each object file may *represent* a source file"). Editing one file recompiles one unit, but relinks everything.

### Object files: code with holes
An object file is the same format as an executable (ELF on Linux) but not runnable:
- **Sections** at address 0: `.text` (code), `.rodata` (string literals), `.data` (initialised globals), `.bss` (zeroed globals; only its size is stored).
- A **symbol table**: `T main` (defined here, in text), `U printf` and `U add` (undefined: used here, defined somewhere else). `nm main.o` prints it.
- **Relocations**: a list of places that must be patched once the final addresses are known, for example "the 4 bytes at offset `0x13`, inside a `call`, must become the distance to `add`". `objdump -dr main.o` shows them inline (`R_X86_64_PLT32 add-0x4`).
- No entry point and no program headers: nothing yet tells the kernel what to map or where to start.

### What the linker does
1. **Symbol resolution.** Every undefined symbol must match exactly one definition in some object file or library. None gives `undefined reference to 'add'`; two give `multiple definition of 'add'`.
2. **Layout.** It merges same-named sections (all `.text` together, then all `.rodata`…) and gives every byte its final virtual address. Now `add` has an address, say `0x117d`.
3. **Relocation.** It patches every hole. A relative call stores *target − address of the next instruction*: a `call` ending at `0x1160` to `add` at `0x117d` gets `1d 00 00 00`.
4. **Output.** It writes the ELF header with the entry point `_start` (from the C runtime's `crt1.o`, which then calls `main`), the program headers the loader maps ([[anatomy-of-a-process]]) and, for a dynamic executable, the name of the loader `/lib64/ld-linux-x86-64.so.2` and the list of needed libraries.

- A **static library** `libfoo.a` is just an archive of `.o` files. The linker takes only the members that resolve something still undefined, scanning left to right, so on the command line libraries must come *after* the objects that use them: `gcc main.o -lfoo`, not `gcc -lfoo main.o`.
- Libraries are connected in one of two ways:

| | Static linking | Dynamic linking |
|---|---|---|
| What goes in the file | Your code plus a copy of every library function used | Your code plus a list of needed shared libraries and versions (`NEEDED: libc.so.6`) |
| File size | Large | Small |
| At start | Nothing to find: map and run | The dynamic loader finds each `.so` on disk and maps it into the process |
| Updates | Rebuild to get a library fix | Update the library once (an OpenSSL security fix) and every program gets it |
| RAM | Each program carries its own copy | One copy of libc's code in RAM, shared by all processes |
| Fails when | Never for missing libraries | A library is missing or too old: `error while loading shared libraries` (Windows: "DLL not found") |
| Examples | Go binaries, Rust with musl, GraalVM native images; `FROM scratch` Docker images | Almost everything on a normal Linux system; `.dll` on Windows, `.dylib` on macOS |

- The lecturer's father-in-law, who keeps statically linked single-file `.exe` programs on a flash drive, is the static column; the "DLL not found" after copying one `.exe` without its libraries is the dynamic one. Docker images solve the same problem today by shipping the program together with its libraries.
- Java has the same choice one level up: a Spring Boot **fat jar** carries all its dependency jars inside (static-like), while a thin jar plus a classpath expects them to be present (dynamic-like).

```anim
linking-static-dynamic
```

### Dynamic linking at run time: PLT, GOT and lazy binding
- Shared libraries are **position-independent**: they can be mapped at any address (and ASLR makes it a random one each run), so the linker cannot write `printf`'s final address into your code. It adds one indirection instead:
  - your code calls a small stub, `printf@plt`, in the **PLT** (procedure linkage table);
  - the stub jumps through a pointer in the **GOT** (global offset table), a writable data area.
- **Lazy binding** (the lecture's "some link lazily at runtime"): initially that GOT slot points back into the loader. The first call runs `ld.so`'s resolver, which looks `printf` up in `libc.so.6`, writes the real address into the GOT and jumps there. Every later call goes stub → GOT → `printf` with no lookup.
- Security-hardened builds (the default in most distributions now) resolve everything at start instead (`-z now`) and then make the GOT read-only (**full RELRO**), so an attacker cannot overwrite a GOT entry to redirect a call. Start-up is slightly slower; calls are the same.
- **`dlopen`** loads a library when the program asks, not at start: plugins, optional codecs, and every JNI library. Java's `System.loadLibrary("netty_transport_native_epoll")` ends in `dlopen`, and its "not found" is `UnsatisfiedLinkError`.
- Where the loader looks, in order: the binary's `RUNPATH`, `LD_LIBRARY_PATH`, the cache built from `/etc/ld.so.conf` (`ldconfig -p`), then `/lib` and `/usr/lib`. `ldd ./app` shows what it would pick, and `LD_DEBUG=libs ./app` shows the search live.

```anim
symbol-relocation
```

### Versions: DLL hell and sonames
- **DLL hell**: old Windows programs loaded libraries by bare name (`MSVCR71.DLL`) from shared system folders. Installer B overwrites the DLL with another version, and program A crashes, because the function signatures or struct layouts changed under it. Windows answered with side-by-side assemblies and, mostly, by having each app carry its own DLLs in its folder.
- Linux puts the **ABI version into the file name**, the **soname**: `libssl.so.3` and `libssl.so.1.1` can be installed together, and a program records which one it needs. A new major version is a new name, so old programs keep the old file.
- glibc goes further with **symbol versions**: one `libc.so.6` contains several versions of some functions, and a binary records the version it was linked against. Built on a new distribution, it fails on an older one with ``version `GLIBC_2.34' not found``. Build on the oldest system you support, or link statically against musl.

### Linkers and why their speed matters
- Linking is single-step and touches everything, so on a big C++ or Rust project it can take longer than compiling the one file you changed, and it repeats on every build.
- The lecture's slide shows the README benchmark of **mold**; the current version (16-core machine, debug builds) reads:

| Output (size) | GNU ld | GNU gold | LLVM lld | mold |
|---|---|---|---|---|
| MySQL 8.3 (0.47 GiB) | 10.84 s | 7.47 s | 1.64 s | 0.46 s |
| Clang 19 (1.56 GiB) | 42.07 s | 33.13 s | 5.20 s | 1.35 s |
| Chromium 124 (1.35 GiB) | n/a | 27.40 s | 6.10 s | 1.52 s |

- The family: **GNU ld** (the classic BFD linker), **gold** (Google, 2008, ELF-only and faster; deprecated in binutils 2.44 in 2025), **lld** (LLVM; its current ELF and COFF linkers were written by **Rui Ueyama**), and **mold** (also by Rui Ueyama, 1.0 in December 2021). So he is the author of lld, not of the original GNU linker the lecture wonders about. The speed comes from parallelism and data structures, not new features.
- Use them without changing the build: `gcc -fuse-ld=mold`, `RUSTFLAGS="-C link-arg=-fuse-ld=mold"`, or `mold -run make`. Rust switched its default linker on x86-64 Linux to lld in Rust 1.90 (September 2025), which cut link times severalfold for free.
- **Link-time optimisation (LTO)** goes the other way: it keeps IR in the object files so the linker can inline across files. Faster program, much slower link.
- The lecturer is right that users never see this; developers do, in every edit-build-test cycle. For a JVM developer the equivalent is a slow Gradle build, not the linker.

### Executable file formats
| | ELF | PE | Mach-O |
|---|---|---|---|
| OS | Linux, BSDs, Android | Windows (`.exe`, `.dll`) | macOS, iOS |
| First bytes | `7f 45 4c 46` ("\x7fELF") | `4d 5a` ("MZ", a DOS header kept from the 1980s), then "PE\0\0" | `cf fa ed fe` (64-bit) |
| Shared libraries | `.so` | `.dll` | `.dylib` |
| Who loads them | kernel + `ld-linux.so` | Windows loader in `ntdll.dll` | kernel + `dyld` |
| Special | `PT_INTERP` names the dynamic loader | the DOS stub prints "This program cannot be run in DOS mode" | **universal binaries** carry x86-64 and ARM64 code in one file (`cafebabe` magic, the same bytes as a Java `.class` file) |

- All three say the same things in different layouts: which CPU, where each segment goes and with what permissions, where to start, which libraries are needed. The ELF layout in detail, with sections vs segments, is in [[anatomy-of-a-process]].
- **Linux could be taught to read PE, and it has been: Wine** loads PE files and reimplements the Win32 API on top of Linux system calls; Valve's Proton (Steam on Linux) is Wine. The format was never the hard part; the API is. WSL 1 did the reverse on Windows.
- **`binfmt_misc`** lets the kernel hand any recognisable file to a registered interpreter: an ARM64 ELF to `qemu-aarch64`, a `.jar` to `java -jar`. That is the mechanism under multi-arch `docker buildx` builds.

### Interpreters and runtimes
- **Write once, run anywhere** works by moving the CPU and OS dependence into one program, the **runtime**: `python3`, `node`, `java`, `dotnet`. The runtime is an ordinary native executable built for every OS and CPU; your program is *data* to it.
- An interpreter does not really go line by line. CPython first compiles the source to **bytecode** (cached as `.pyc` in `__pycache__`), then runs a loop: fetch the next bytecode, jump to its handler (a big `switch` or computed `goto`), execute, repeat. `c = a + b` becomes three bytecodes in Python 3.13 (`LOAD_FAST_LOAD_FAST a, b`, `BINARY_OP +`, `STORE_FAST c`; `python3 -m dis` shows them), and each costs dozens of machine instructions: type checks, reference count updates, the dispatch jump itself. That overhead, CPU spent finding out what to do instead of doing it, is why pure interpretation is 10 to 100 times slower than compiled code.
- **"Compiled" or "interpreted" describes an implementation, not a language:**

| Language | Main implementation | How code reaches the CPU |
|---|---|---|
| C, C++, Rust | gcc, clang, rustc | AOT to machine code; no runtime besides libc |
| Go | gc | AOT to machine code; the runtime (scheduler, GC) is **linked into every binary** |
| Java, Kotlin | javac + HotSpot | AOT to bytecode, then interpreted and JIT-compiled; or AOT to machine code with GraalVM native-image |
| C# | .NET | bytecode (IL) + JIT; Native AOT since .NET 7 |
| JavaScript | V8 (Chrome, Node) | Ignition interpreter, then the Sparkplug, Maglev and TurboFan JITs |
| TypeScript | tsc | *transpiled* to JavaScript, then as above |
| Python | CPython | bytecode interpreter; an experimental JIT since 3.13, off by default (`PYTHON_JIT=1`); PyPy is a separate JIT implementation |

- So the slide's "Python/JavaScript/Java" list is one interpreter and two JIT runtimes. Java was never "interpreted only": HotSpot has had a JIT since 1999, and the JVM specifics (tiers, deoptimisation, warm-up) are in [[compilation-interpretation-jit]].
- The lecturer's complaint, that the user must install the right runtime first, is real, and each ecosystem has its answer: a Docker image with the JRE inside, a custom minimal runtime with `jlink`, a GraalVM native image, .NET "self-contained" publishing, `bun build --compile` and `deno compile` for JavaScript, or just Go. For a backend that only ever runs in your container, portability matters less than startup, memory and hiring.

### JIT compilation at the machine level
A JIT has to create code while the process runs, which collides with how the OS protects memory. Step by step:
1. **Count.** The interpreter keeps counters per function and loop. When one crosses a threshold, the code is "hot".
2. **Compile.** The JIT translates it into machine code for *this* CPU, using what it observed (which types actually arrive, which branches are taken).
3. **Get writable memory.** Not the `malloc` heap: a separate region from `mmap`, the **code cache**. Pages are mapped read-write.
4. **Make it executable.** On most systems a page should be writable *or* executable, never both (**W^X**), so after writing, the JIT calls `mprotect(PROT_READ | PROT_EXEC)`. Some JITs map the same physical pages twice, one writable view and one executable view. On ARM the JIT must also flush the instruction cache, which does not see the writes made through the data cache.
5. **Jump.** It calls the new code through a function pointer, or patches the interpreter's dispatch entry or call sites to point at it. The program counter is now in the code cache, and the CPU runs it natively.

- **Who enforces "not executable"?** Not the kernel checking each instruction, as the lecture puts it. Each page table entry has an **NX** (no-execute) bit, and the **MMU** checks it in hardware on every instruction fetch. Fetching from a non-executable page causes a page fault; the kernel turns it into `SIGSEGV`. That is why a buffer overflow that writes shellcode onto the stack or heap cannot simply run it.
- W^X is policy, not physics. HotSpot on Linux x86-64 maps its code cache read-write-execute: `grep rwxp /proc/<pid>/maps` on a JDK 21 process shows three regions, the three segments of the code cache. Apple Silicon forces the strict model: a JIT needs `MAP_JIT` and flips each thread between writing and executing with `pthread_jit_write_protect_np`. iOS forbids JIT for ordinary apps, which is why every iOS browser engine had to be Apple's.
- Hardened systems can forbid writable-then-executable memory altogether (SELinux `execmem`, systemd `MemoryDenyWriteExecute=yes`). The JVM and Node then fail at startup, so this is a setting you may meet on locked-down servers.

```anim
jit-hot-path
```

### Garbage collection lives in the runtime
- Four ways to free memory:

| Approach | Who frees | Languages | Cost |
|---|---|---|---|
| Manual | You call `free` / `delete` | C, C++ (RAII and smart pointers automate most of it) | Leaks, double frees, use-after-free ([[anatomy-of-a-process]]) |
| Ownership | The **compiler** inserts the free where the owner goes out of scope | Rust | Learning curve; no runtime work |
| Reference counting | The runtime, when a count reaches 0 | CPython, Swift (ARC), Rust's `Rc` | A memory write on every reference change; cycles need an extra collector |
| Tracing GC | The runtime: mark everything reachable from roots, reclaim the rest | Java, Go, C#, JavaScript | Headers, barriers, CPU, pauses, memory headroom |

- **A GC needs runtime code, not an installed runtime.** Go has a concurrent tracing GC and still ships one static binary, because the runtime is linked in. The same is true for a GraalVM native image (it includes a small GC) and .NET Native AOT. The lecture's "C# doesn't run without the runtime" is true only for the default deployment.
- **Costs**, more specifically than "tags every object":
  - every object carries a **header**: 12 to 16 bytes in HotSpot, 8 with compact object headers (`-XX:+UseCompactObjectHeaders`, a product option since JDK 25);
  - the JIT inserts **barriers** around reference reads or writes, so the collector can work while the program runs;
  - the collector uses **CPU** of its own, often whole cores under high allocation rates;
  - a tracing GC wants **headroom**, roughly two to three times the live data, to run efficiently.
- **Pauses.** The lecture explains them with mutexes on objects; HotSpot instead stops all application threads at **safepoints** (points where every thread's stack and registers are in a known state), does the work that cannot run concurrently, and resumes them. Old collectors stopped for the whole collection; G1 targets a pause goal (200 ms by default), and ZGC and Shenandoah do almost everything concurrently with pauses well under a millisecond. Go's collector is concurrent too, with short pauses, at the cost of more CPU. Details: [[garbage-collection]].
- **The Linkerd story, with the details right.** Linkerd 1.x (2016) was written in **Scala** on the JVM (Finagle and Netty). As a sidecar next to every service instance, it was hard to keep below about 150 MB of resident memory, needed JVM tuning, and some teams refused to run a JVM in the request path at all; GC pauses showed up as tail latency. The team rewrote it: the 2.x **data-plane proxy in Rust** (well under 10 MB, p99 under a millisecond, memory freed as each request ends) and the **control plane in Go**. The rewrite first shipped in late 2017 as **Conduit**, so as not to risk the Linkerd name, and was merged back as **Linkerd 2.0** in September 2018.
- The lesson for a Java backend is narrower than "rewrite in Rust": a sidecar runs once per pod, so its memory is multiplied by thousands, and it sits on every request's path. For a normal service, choose and tune the collector first ([[garbage-collection]]).

## Example
A two-file C program through every stage, on Linux x86-64. `main.c` calls `add` from `math.c` and `printf` from libc:
```bash
$ cat math.c
int add(int a, int b) { return a + b; }
$ cat main.c
#include <stdio.h>
int add(int a, int b);
int main(void) { printf("a + b = %d\n", add(1, 3)); return 0; }

$ gcc -c main.c math.c            # compile + assemble: main.o, math.o
$ nm main.o
                 U add             # used here, defined elsewhere
0000000000000000 T main            # defined here, address 0 for now
                 U printf
$ objdump -dr main.o | grep -A1 'call'
  12:   e8 00 00 00 00   call   17 <main+0x17>
                         13: R_X86_64_PLT32   add-0x4     # the hole and how to fill it
  28:   e8 00 00 00 00   call   2d <main+0x2d>
                         29: R_X86_64_PLT32   printf-0x4

$ gcc main.o math.o -o app        # link
$ objdump -d app | grep 'call.*<add>'
    115b:   e8 1d 00 00 00   call   117d <add>            # 0x1160 + 0x1d = add
$ readelf -h app | grep Entry
  Entry point address:               0x1060               # _start, not main
$ ldd app
        libc.so.6 => /lib/x86_64-linux-gnu/libc.so.6
        /lib64/ld-linux-x86-64.so.2
$ gcc -static main.o math.o -o app-static && ls -lh app app-static
  16K app
 767K app-static                                          # libc's printf, malloc… copied in
$ LD_DEBUG=bindings ./app 2>&1 | grep printf
binding file ./app [0] to /lib/x86_64-linux-gnu/libc.so.6 [0]: normal symbol `printf' [GLIBC_2.2.5]
$ readelf -d app | grep FLAGS
 0x000000000000001e (FLAGS)              BIND_NOW         # Ubuntu links with -z now: resolved at start
```
This is real output from GCC 13 on Ubuntu 24.04; offsets and sizes vary with the compiler version, the shape does not. Link with `-Wl,-z,lazy` to get lazy binding, where the same `LD_DEBUG` line appears only when `printf` is first called.

A JIT in 20 lines: put machine code bytes into fresh memory, make it executable and jump to it. This is exactly what HotSpot and V8 do, with a compiler in front instead of hand-written bytes:
```c
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>

int main(void) {
#if defined(__x86_64__)
    unsigned char code[] = { 0xb8, 0x2a, 0, 0, 0,     /* mov eax, 42 */
                             0xc3 };                  /* ret         */
#elif defined(__aarch64__)
    unsigned int code[] = { 0x52800540,               /* mov w0, #42 */
                            0xd65f03c0 };             /* ret         */
#endif
    void *mem = mmap(NULL, 4096, PROT_READ | PROT_WRITE,          /* 1. writable, not executable */
                     MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    memcpy(mem, code, sizeof code);                               /* 2. "compile" into it        */
    __builtin___clear_cache(mem, (char *)mem + sizeof code);      /*    needed on ARM            */
    mprotect(mem, 4096, PROT_READ | PROT_EXEC);                   /* 3. W^X: now executable only */
    int (*fn)(void) = (int (*)(void))mem;
    printf("%d\n", fn());                                         /* 4. PC jumps into it: 42     */
}
```
Remove the `mprotect` line and the call crashes with `SIGSEGV`: the MMU refuses to fetch instructions from a page without execute permission. (On macOS on Apple Silicon this exact code fails differently; there you need `MAP_JIT`.)

## Pitfalls
- **`./app: not found` for a file that exists.** The binary was linked against glibc and runs on Alpine (musl), which has no `/lib64/ld-linux-x86-64.so.2`; `execve` reports the missing loader as a missing file. The same happens to JNI libraries inside Java apps on Alpine images (Netty native transports, snappy, RocksDB). Use a glibc-based image (Debian slim, Ubuntu, distroless) or musl builds of the libraries.
- **`noexec` on `/tmp`.** Libraries like snappy-java, Netty native and RocksDB extract their `.so` into `java.io.tmpdir` and `dlopen` it. On hardened hosts or read-only Kubernetes pods with a `noexec` temp dir this fails with `failed to map segment from shared object`. Point `-Djava.io.tmpdir` (or `-Dio.netty.native.workdir`) at a writable, executable directory.
- **``version `GLIBC_2.xx' not found``** after building a native tool or JNI library on a newer machine than the one it runs on. Build in the oldest supported distribution or a container of it.
- **`exec format error`**: right OS, wrong CPU, typically an `amd64` image on an ARM64 node. Publish multi-arch images (`docker buildx build --platform linux/amd64,linux/arm64`).
- **Link order**: `undefined reference` although the library is on the command line usually means it appears *before* the object that needs it.
- **Treating "interpreted" as "slow" or "JIT" as "fast" without measuring**: a JIT needs warm-up, and the first requests after a deploy run in the interpreter ([[compilation-interpretation-jit]]).
- **Blaming the GC by default**: before switching collectors or languages, check the allocation rate and the heap headroom in the GC logs; a GC that runs constantly is usually a sign of too small a heap or too much garbage.
- Related: [[anatomy-of-a-process]] for the ELF layout and how `execve` starts the result, [[memory-management]] for page tables and the protection bits a JIT works around, [[compilation-interpretation-jit]] for HotSpot's tiers, [[garbage-collection]] for the JVM's collectors, [[virtualization-and-containers]] for why images ship their own libraries.
