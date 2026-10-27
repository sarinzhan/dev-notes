---
title: Хранение файлов и блобов — объектное хранилище в стиле S3, presigned URL, multipart upload
tags: [system-design, storage, s3, files]
learned: 2026-10-27
---
## Кратко
Файлам не место ни в базе данных, ни на диске pod. Объектное хранилище (object storage) (S3, GCS, MinIO on-prem) хранит неизменяемые блобы под ключом в бакете — с репликацией, версионированием, доступом по HTTP, правилами жизненного цикла и стоимостью за GB на порядок ниже, чем у базы данных. Приложение хранит **метаданные** (владелец, размер, тип контента, ключ) в Postgres и никогда не передаёт байты само: клиенты загружают файлы по **presigned URL** напрямую в бакет, скачивают через presigned URL или [[cdn]], а большие файлы идут **multipart**-частями, каждая из которых повторяется независимо. Сложности — согласованность между строкой метаданных и объектом, уборка «сирот» и контроль доступа по URL, которыми может воспользоваться любой, у кого они есть.

## Основное

### Свойства объектного хранилища
- **Плоское пространство ключей**: `bucket/2026/10/27/<uuid>.pdf`; «папки» — это префиксы. Листинг по префиксу — медленная операция с пагинацией; никогда не проектируй процесс, который ищет файл листингом, ищи его в таблице метаданных.
- **Неизменяемые объекты**: `PUT` заменяет объект целиком; дописывания или частичного обновления нет. Версионирование хранит старые версии, правила жизненного цикла их удаляют.
- **Долговечность (durability)** (11 девяток) за счёт репликации между зонами; доступность около 99,9–99,99 %; сильная согласованность чтения после записи (read-after-write) для новых объектов сейчас есть у всех крупных провайдеров.
- **Пропускная способность масштабируется по префиксам**, а не по бакету: тысячи запросов/с на префикс; большие объекты передаются со скоростью сотни MB/с. Задержка запроса 10–100 мс, поэтому это не хранилище для миллионов крошечных объектов, читаемых по одному ([[storage-choices]]).
- **Модель стоимости**: за GB-месяц по классу хранения (горячий, редкий доступ, архив), за запрос и за GB исходящего трафика. Исходящий трафик через CDN дешевле и быстрее, чем из бакета.

### Загрузка через presigned URL
1. Клиент вызывает API: `POST /files` с именем, размером, типом контента. Сервис проверяет (лимит размера, разрешённые типы, квота), создаёт строку метаданных со `status = PENDING` и сгенерированным ключом и возвращает presigned `PUT` URL (истекает через 5–15 мин, привязан к ключу, типу контента и максимальному размеру).
2. Клиент загружает байты **напрямую в бакет**. Pod API никогда не видят полезную нагрузку, поэтому загрузка 2 GB не тратит ни памяти приложения, ни потоков, ни пропускной способности.
3. Клиент вызывает `POST /files/{id}/complete` (или бакет отправляет событие); сервис проверяет, что объект существует (`HEAD`, совпадают размер и контрольная сумма), и ставит `status = READY`.
4. Плановая задача удаляет строки `PENDING` старше часа и их частичные объекты ([[background-jobs-and-schedulers]]).

Скачивание устроено зеркально: presigned `GET` с коротким сроком или подписанный URL CDN для условно публичных медиа. Подпись — это HMAC от ключа + срока + метода, вычисленный с учётными данными бакета; бакет проверяет её без обращения к твоему сервису ([[hashing-vs-encryption]]).

### Multipart upload
- Для объектов больше ~100 MB: инициировать загрузку, параллельно загрузить части от 5 MB до 5 GB (каждая часть повторяется при сбое), затем завершить, передав список ETag частей. Бакет собирает объект. Неудачная загрузка оставляет части, за которые платишь, пока их не уберёт **abort** или правило жизненного цикла (`AbortIncompleteMultipartUpload` через 1 день).
- Каждая часть получает свой presigned URL; клиент загружает 4–8 частей одновременно, что к тому же лучше загружает канал, чем один поток.
- Возобновление: клиент получает список загруженных частей и продолжает; так мобильные приложения переживают потерю соединения во время загрузки видео на 500 MB.

### Метаданные и согласованность
- Строка в Postgres — истина о том, *какие файлы есть и у кого*; объект — истина о *байтах*. Две записи — два вида сбоев: строка без объекта (загрузка не завершилась) и объект без строки (вставка строки упала после загрузки). Состояния `PENDING → READY` плюс задача очистки покрывают оба; никогда не доверяй URL, за которым нет строки `READY`.
- Удаление: пометить строку удалённой, опубликовать событие, асинхронно удалить объект с retry; прямое синхронное удаление, которое упало, оставляет висящий объект, который никто не отслеживает ([[outbox-pattern]]).
- Контрольные суммы: отправляй `Content-MD5`/SHA-256 при загрузке, сохраняй, проверяй при завершении; тихая порча данных на нестабильном мобильном канале — реальность.

### Безопасность
- Бакеты по умолчанию приватные; весь доступ — через presigned URL со временем жизни в минуты или через приложение для чувствительных данных, где важен аудит каждого чтения.
- Имена ключей не должны угадываться (UUID, а не `invoice-1001.pdf`), а подпись покрывает ключ, поэтому URL для одного объекта нельзя превратить в URL для другого.
- Шифрование на стороне сервера при хранении (at rest) ключами провайдера или клиента; для регулируемых данных (документы абонентов, договоры) — отдельный бакет, закреплённый за регионом, с логированием доступа ([[secrets-management]] — про ключи, [[requirements-and-constraints]] — про требования к размещению данных).
- Проверяй тип контента по байтам, а не по заголовку; отдавай пользовательские загрузки с `Content-Disposition: attachment` или с отдельного домена, чтобы исключить выполнение скриптов на основном origin ([[owasp-top-10-for-backend]]).

### Жизненный цикл и стоимость
- Правила по префиксам: перенос в класс редкого доступа через 30 дней, в архив через 1 год, удаление после срока хранения; здесь 90 % экономии.
- Версионирование включено на бакетах с пользовательскими данными (отмена случайных удалений), выключено для производных данных (миниатюры), которые можно сгенерировать заново.
- Исходящий трафик через CDN с длинными TTL для неизменяемых ключей ([[cdn]]).

## Пример
Эндпоинты presigned-загрузки и завершения на AWS SDK v2 (работает с MinIO через переопределение endpoint), метаданные в Postgres, multipart для больших файлов.

```java
@RestController
@RequestMapping("/v1/files")
public class FileController {
    private static final long MAX_SIMPLE = 100L * 1024 * 1024;        // выше этого порога: multipart
    private final S3Presigner presigner; private final S3Client s3; private final FileRepository files;
    @Value("${storage.bucket}") private String bucket;

    public FileController(S3Presigner presigner, S3Client s3, FileRepository files) {
        this.presigner = presigner; this.s3 = s3; this.files = files;
    }

    public record CreateRequest(String fileName, long sizeBytes, String contentType, String sha256) {}
    public record CreateResponse(UUID id, String uploadUrl, String uploadId, List<String> partUrls) {}

    @PostMapping
    public CreateResponse create(@RequestBody CreateRequest req, @AuthenticationPrincipal Subscriber me) {
        if (req.sizeBytes() > 5L * 1024 * 1024 * 1024 || !AllowedTypes.contains(req.contentType()))
            throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "size or type not allowed");
        UUID id = UUID.randomUUID();
        String key = "subscribers/%d/%s".formatted(me.id(), id);          // не угадывается, владелец в префиксе
        files.insertPending(id, me.id(), key, req);                        // status = PENDING

        if (req.sizeBytes() <= MAX_SIMPLE) {
            var put = PutObjectRequest.builder().bucket(bucket).key(key)
                    .contentType(req.contentType()).contentLength(req.sizeBytes()).build();
            String url = presigner.presignPutObject(p -> p.putObjectRequest(put)
                    .signatureDuration(Duration.ofMinutes(10))).url().toString();
            return new CreateResponse(id, url, null, List.of());
        }
        String uploadId = s3.createMultipartUpload(b -> b.bucket(bucket).key(key).contentType(req.contentType())).uploadId();
        int parts = (int) Math.ceil(req.sizeBytes() / (double) (16L * 1024 * 1024));   // части по 16 MB
        List<String> partUrls = new ArrayList<>(parts);
        for (int n = 1; n <= parts; n++) {
            final int partNumber = n;
            partUrls.add(presigner.presignUploadPart(p -> p.uploadPartRequest(u -> u.bucket(bucket).key(key)
                    .uploadId(uploadId).partNumber(partNumber)).signatureDuration(Duration.ofHours(2))).url().toString());
        }
        files.saveUploadId(id, uploadId);
        return new CreateResponse(id, null, uploadId, partUrls);
    }

    @PostMapping("/{id}/complete")
    public void complete(@PathVariable UUID id, @RequestBody(required = false) List<CompletedPart> parts,
                         @AuthenticationPrincipal Subscriber me) {
        FileRow f = files.findOwned(id, me.id()).orElseThrow();
        if (f.uploadId() != null)
            s3.completeMultipartUpload(b -> b.bucket(bucket).key(f.key()).uploadId(f.uploadId())
                    .multipartUpload(m -> m.parts(parts)));
        HeadObjectResponse head = s3.headObject(b -> b.bucket(bucket).key(f.key()));   // существует, размер совпадает
        if (head.contentLength() != f.sizeBytes())
            throw new ResponseStatusException(HttpStatus.CONFLICT, "uploaded size mismatch");
        files.markReady(id);                                              // URL для скачивания выдаются только для READY-файлов
    }

    @GetMapping("/{id}/url")
    public String downloadUrl(@PathVariable UUID id, @AuthenticationPrincipal Subscriber me) {
        FileRow f = files.findReadyOwned(id, me.id()).orElseThrow();
        return presigner.presignGetObject(p -> p.getObjectRequest(g -> g.bucket(bucket).key(f.key())
                .responseContentDisposition("attachment; filename=\"" + f.fileName() + "\""))
                .signatureDuration(Duration.ofMinutes(5))).url().toString();
    }
}
```

На бакете настроено правило жизненного цикла, прерывающее незавершённые multipart-загрузки через один день, а задача очистки удаляет строки `PENDING` старше двух часов.

## Подводные камни
- Передача загрузок через pod API: узким местом становятся память, потоки и лимиты тела запроса на балансировщике; загрузка 1 GB занимает поток Tomcat на минуты.
- «Временно» публичные бакеты или долгоживущие presigned URL, пересланные в чате: данные публичны до истечения срока; держи срок в минутах, привязывай к объекту, логируй доступ.
- Листинг бакета, чтобы найти файлы пользователя: медленно, с пагинацией и гонками; метаданные живут в базе данных.
- «Сироты» с обеих сторон: объекты без строк и строки без объектов; состояние `PENDING`, правила жизненного цикла и задача сверки обязательны.
- Файлы в `bytea` или на томе pod: бэкапы раздуваются, репликация отстаёт, pod нельзя свободно заменять ([[horizontal-vs-vertical-scaling]], [[postgres-vacuum-and-bloat]]).
- Связанные заметки: [[cdn]] — отдача сохранённого; [[storage-choices]] — место блобов среди других хранилищ.
