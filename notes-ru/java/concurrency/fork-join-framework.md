---
title: Фреймворк Fork/Join
tags: [fork-join, parallelism, work-stealing, parallel-streams]
learned: 2026-10-22
---
## Кратко
Fork/Join (Java 7) выполняет задачи по принципу «разделяй и властвуй»: большая задача рекурсивно делится (**fork**) на подзадачи, пока они не станут достаточно маленькими, чтобы вычислить их напрямую, затем результаты объединяются (**join**). `ForkJoinPool` держит своих рабочих занятыми через **перехват работы** (work stealing), и именно он лежит в основе параллельных стримов и executor по умолчанию для `CompletableFuture`.

## Основное
- `ForkJoinPool`: специализированный `ExecutorService`, в котором у каждого рабочего своя двусторонняя очередь. Рабочий кладёт и забирает свои подзадачи с одного конца; простаивающий рабочий перехватывает задачи с *другого* конца очереди занятого рабочего. Минимальная конкуренция, хорошая локальность.
- `RecursiveTask<V>`: подзадача, возвращающая результат; реализуется `V compute()`.
- `RecursiveAction`: подзадача без результата.
- `ForkJoinPool.commonPool()` общий на всю JVM, размер — `cores - 1`. Его используют параллельные стримы и `CompletableFuture.supplyAsync` без executor.
- Порядок работы: отправить корневую задачу → в `compute()` решить, достаточно ли задача мала → если нет, `fork()` левой половины, `compute()` правой половины напрямую, `join()` левой → вернуть объединённый результат.

## Пример
```java
class SumTask extends RecursiveTask<Long> {
    private static final int THRESHOLD = 10_000;
    private final long[] arr; private final int lo, hi;
    SumTask(long[] arr, int lo, int hi) { this.arr = arr; this.lo = lo; this.hi = hi; }

    @Override protected Long compute() {
        if (hi - lo <= THRESHOLD) {
            long s = 0; for (int i = lo; i < hi; i++) s += arr[i]; return s;
        }
        int mid = (lo + hi) >>> 1;
        SumTask left = new SumTask(arr, lo, mid);
        left.fork();                                   // выполнить асинхронно
        long right = new SumTask(arr, mid, hi).compute(); // выполнить здесь
        return left.join() + right;
    }
}
long total = ForkJoinPool.commonPool().invoke(new SumTask(data, 0, data.length));
```

## Подводные камни
- Блокирующий ввод-вывод внутри задачи fork/join душит общий пул и замедляет каждый параллельный стрим в JVM. Fork/Join — для работы, ограниченной процессором; для ввода-вывода используй [[executors-and-thread-pools]] или [[virtual-threads]].
- Слишком маленький порог топит работу в накладных расходах на задачи; слишком большой теряет параллелизм. Хорошая цель — тысячи листовых задач.
- `fork()` обеих половин и `join()` обеих впустую тратит текущего рабочего; одну половину вычисляй напрямую, как выше.
- Связанные заметки: [[completable-future]] по умолчанию работает на общем пуле.
