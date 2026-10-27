---
title: File and blob storage — S3-style object storage, presigned URLs, multipart upload
tags: [system-design, storage, s3, files]
learned: 2026-10-27
---
## TL;DR
Files do not belong in a database or on a pod's disk. Object storage (S3, GCS, MinIO on-prem) stores immutable blobs under a key in a bucket, replicated, versioned, with HTTP access, lifecycle rules and cost per GB that is an order of magnitude below a database's. The application keeps **metadata** (owner, size, content type, key) in Postgres and never streams the bytes itself: clients upload with a **presigned URL** directly to the bucket, download via a presigned URL or a [[cdn]], and big files go in **multipart** chunks that are retried independently. The hard parts are consistency between the metadata row and the object, cleaning up orphans, and access control on URLs that anyone holding them can use.

## Key points

### Properties of object storage
- **Flat key space**: `bucket/2026/10/27/<uuid>.pdf`; "folders" are prefixes. Listing by prefix is a slow, paginated operation; never design a flow that lists to find a file, look it up in the metadata table.
- **Immutable objects**: `PUT` replaces the whole object; there is no append or partial update. Versioning keeps old versions; lifecycle rules expire them.
- **Durability** (11 nines) through replication across zones; availability around 99.9–99.99 %; strong read-after-write consistency for new objects on all major providers now.
- **Throughput scales with prefixes**, not with the bucket: thousands of requests/s per prefix; large objects stream at hundreds of MB/s. Latency per request is 10–100 ms, so it is not a store for millions of tiny objects read one by one ([[storage-choices]]).
- **Cost model**: per GB-month by storage class (hot, infrequent, archive), per request, and per GB of egress. Egress through a CDN is cheaper and faster than from the bucket.

### Upload flow with presigned URLs
1. Client calls the API: `POST /files` with name, size, content type. The service validates (size limit, allowed types, quota), creates a metadata row with `status = PENDING` and a generated key, and returns a presigned `PUT` URL (expires in 5–15 min, bound to the key, content type and max size).
2. Client uploads the bytes **directly to the bucket**. The API pods never see the payload, so a 2 GB upload costs no application memory, threads or bandwidth.
3. Client calls `POST /files/{id}/complete` (or the bucket emits an event); the service verifies the object exists (`HEAD`, size and checksum match) and sets `status = READY`.
4. A scheduled job deletes `PENDING` rows older than an hour and their partial objects ([[background-jobs-and-schedulers]]).

Downloads mirror this: presigned `GET` with a short expiry, or a signed CDN URL for public-ish media. The signature is an HMAC over key + expiry + method computed with the bucket credentials; the bucket verifies it without a call to your service ([[hashing-vs-encryption]]).

### Multipart upload
- For objects over ~100 MB: initiate, upload parts of 5 MB–5 GB in parallel (each part retried on failure), then complete with the list of part ETags. The bucket assembles the object. A failed upload leaves parts that cost money until an **abort** or a lifecycle rule (`AbortIncompleteMultipartUpload` after 1 day) cleans them.
- Each part gets its own presigned URL; the client uploads 4–8 parts concurrently, which also saturates the link better than one stream.
- Resume: the client lists uploaded parts and continues; this is how mobile apps survive a lost connection during a 500 MB video upload.

### Metadata and consistency
- The Postgres row is the truth about *which files exist for whom*; the object is the truth about *bytes*. Two writes, two failure modes: row without object (upload never completed) and object without row (row insert failed after upload). The `PENDING → READY` state plus a cleanup job covers both; never trust a URL that is not backed by a `READY` row.
- Delete: mark the row deleted, publish an event, delete the object asynchronously with retries; a direct synchronous delete that fails leaves a dangling object that nobody tracks ([[outbox-pattern]]).
- Checksums: send `Content-MD5`/SHA-256 on upload, store it, verify on complete; silent corruption on a flaky mobile link is real.

### Security
- Buckets private by default; all access through presigned URLs with minutes of lifetime, or through the application for sensitive data where auditing every read matters.
- Key names must not be guessable (UUIDs, not `invoice-1001.pdf`), and the signature covers the key, so a URL for one object cannot be turned into another.
- Server-side encryption at rest with provider-managed or customer-managed keys; for regulated data (subscriber documents, contracts) a dedicated bucket, region pinned, access logged ([[secrets-management]] for the keys, [[requirements-and-constraints]] for residency).
- Validate content type by sniffing the bytes, not by trusting the header; serve user uploads with `Content-Disposition: attachment` or from a separate domain to avoid script execution in the main origin ([[owasp-top-10-for-backend]]).

### Lifecycle and cost
- Rules per prefix: move to infrequent access after 30 days, archive after 1 year, delete after the retention period; this is where 90 % of the cost savings are.
- Versioning on buckets with user data (undo accidental deletes), off for derived data (thumbnails) that can be regenerated.
- Egress through a CDN with long TTLs for immutable keys ([[cdn]]).

## Example
Presigned upload and completion endpoints with the AWS SDK v2 (works against MinIO with an endpoint override), metadata in Postgres, multipart for large files.

```java
@RestController
@RequestMapping("/v1/files")
public class FileController {
    private static final long MAX_SIMPLE = 100L * 1024 * 1024;        // above this: multipart
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
        String key = "subscribers/%d/%s".formatted(me.id(), id);          // unguessable, owner in the prefix
        files.insertPending(id, me.id(), key, req);                        // status = PENDING

        if (req.sizeBytes() <= MAX_SIMPLE) {
            var put = PutObjectRequest.builder().bucket(bucket).key(key)
                    .contentType(req.contentType()).contentLength(req.sizeBytes()).build();
            String url = presigner.presignPutObject(p -> p.putObjectRequest(put)
                    .signatureDuration(Duration.ofMinutes(10))).url().toString();
            return new CreateResponse(id, url, null, List.of());
        }
        String uploadId = s3.createMultipartUpload(b -> b.bucket(bucket).key(key).contentType(req.contentType())).uploadId();
        int parts = (int) Math.ceil(req.sizeBytes() / (double) (16L * 1024 * 1024));   // 16 MB parts
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
        HeadObjectResponse head = s3.headObject(b -> b.bucket(bucket).key(f.key()));   // exists, size matches
        if (head.contentLength() != f.sizeBytes())
            throw new ResponseStatusException(HttpStatus.CONFLICT, "uploaded size mismatch");
        files.markReady(id);                                              // only READY files get download URLs
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

The bucket gets a lifecycle rule aborting incomplete multipart uploads after one day, and a cleanup job removes `PENDING` rows older than two hours.

## Pitfalls
- Streaming uploads through the API pod: memory, threads and the load balancer's body limits become the bottleneck; a 1 GB upload ties up a Tomcat thread for minutes.
- Public buckets "temporarily" or long-lived presigned URLs shared in chat: the data is public until expiry; keep minutes, bind to the object, log access.
- Listing the bucket to find a user's files: slow, paginated and racy; metadata lives in the database.
- Orphans from both sides: objects without rows and rows without objects; the `PENDING` state, lifecycle rules and a reconciliation job are not optional.
- Files in `bytea` or on the pod's volume: backups balloon, replication lags, pods cannot be replaced freely ([[horizontal-vs-vertical-scaling]], [[postgres-vacuum-and-bloat]]).
- Related: [[cdn]] for serving what you stored; [[storage-choices]] for where blobs sit among the other stores.
