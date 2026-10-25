---
title: Request validation and file uploads in Spring MVC
tags: [spring-mvc, validation, bean-validation, multipart]
learned: 2026-10-22
---
## TL;DR
Validation uses the **Bean Validation API** (Jakarta Validation 3, Hibernate Validator as implementation, via `spring-boot-starter-validation`): annotate DTO fields with `@NotNull`, `@Size`, `@Pattern` and put `@Valid` on the controller parameter; a failure becomes `MethodArgumentNotValidException` → HTTP 400. `@Validated` adds validation groups and method-level validation on services. **File uploads** arrive as `multipart/form-data` and are bound to `MultipartFile` parameters; size limits are properties.

## Key points
### Bean Validation
- Constraints: `@NotNull`, `@NotBlank` (strings), `@NotEmpty` (collections), `@Size`, `@Min`/`@Max`, `@Positive`, `@Email`, `@Pattern`, `@Past`/`@Future`, `@Digits`. Nested objects need `@Valid` on the field to cascade; collections: `List<@Valid Item>`.
- `@Valid` (Jakarta) on a `@RequestBody` / `@ModelAttribute` parameter triggers validation during argument resolution. The error is `MethodArgumentNotValidException` (body) or `BindException` (form/query), both mapped to 400 by `DefaultHandlerExceptionResolver`; with `spring.mvc.problemdetails.enabled=true` you get a `ProblemDetail` body.
- `@Validated` (Spring) is the extended version: supports **groups** (`@Validated(OnCreate.class)`) and, when placed on a **class**, turns on method validation through an AOP proxy ([[spring-aop]]), so `@NotNull` on service method parameters and `@Valid` on return values are checked; the exception is `ConstraintViolationException`. Since Spring 6.1 controller method parameters like `@RequestParam @Min(1) int page` are validated without `@Validated` on the class.
- Custom constraint: an annotation with `@Constraint(validatedBy = ...)` and a `ConstraintValidator<Annotation, Type>` implementation; validators are Spring beans, so you can inject a repository to check uniqueness.
- Custom validator the Spring way: implement `org.springframework.validation.Validator` (`supports`, `validate(target, Errors)`) and register it with `@InitBinder` in the controller, or call it manually. Useful for cross-field rules that are awkward as annotations.
- Records work: `record CreateEsimRequest(@NotBlank String msisdn, @Pattern(regexp = "\\d{19,20}") String iccid) {}`.

### Multipart uploads
- The client sends `Content-Type: multipart/form-data` (the frontend form or `FormData` must set it; do not set it manually with a boundary in JS). Spring's `StandardServletMultipartResolver` parses it and exposes each part as `MultipartFile` (`getOriginalFilename()`, `getContentType()`, `getSize()`, `getInputStream()`, `transferTo(Path)`).
- Bind with `@RequestParam("file") MultipartFile file` or `@RequestPart` (the latter also deserializes a JSON part next to the file).
- Limits (Boot properties): `spring.servlet.multipart.max-file-size=10MB` per file, `spring.servlet.multipart.max-request-size=10MB` for the whole request, `file-size-threshold` above which parts are written to disk, `location` for the temp dir. Exceeding them throws `MaxUploadSizeExceededException` (map it to 413).
- For large files stream `getInputStream()` to S3/MinIO instead of `getBytes()`, and validate the real content type (magic bytes), not the header.

## Example
```java
public record CreateEsimRequest(
        @NotBlank @Pattern(regexp = "996\\d{9}") String msisdn,
        @NotBlank @Size(min = 19, max = 20) String iccid,
        @NotNull @Valid Plan plan) {
    public record Plan(@NotBlank String code, @Positive int days) {}
}

@RestController
@RequestMapping("/api/v1/esims")
public class EsimImportController {

    @PostMapping(consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    @ResponseStatus(HttpStatus.ACCEPTED)
    public ImportJobDto importCsv(@RequestPart("file") MultipartFile file,
                                  @RequestPart("meta") @Valid ImportMeta meta) throws IOException {
        if (!"text/csv".equals(file.getContentType())) {
            throw new UnsupportedMediaTypeStatusException("CSV expected");
        }
        try (var in = file.getInputStream()) {
            return importService.start(in, meta);
        }
    }
}

@RestControllerAdvice
class ValidationErrors {
    @ExceptionHandler(MethodArgumentNotValidException.class)
    ProblemDetail invalid(MethodArgumentNotValidException e) {
        var pd = ProblemDetail.forStatus(HttpStatus.BAD_REQUEST);
        pd.setProperty("errors", e.getFieldErrors().stream()
                .map(f -> f.getField() + ": " + f.getDefaultMessage()).toList());
        return pd;
    }
}
```
```yaml
spring:
  servlet:
    multipart:
      max-file-size: 10MB
      max-request-size: 12MB
```

## Pitfalls
- Forgetting `@Valid` on the parameter: constraints exist but nothing runs them. Also forgetting `spring-boot-starter-validation` (Boot 2.3+ no longer includes it in `starter-web`).
- `@Valid` on a nested field is needed for cascading; `@NotNull` on the parent does not validate children.
- `@Validated` on a service class creates a proxy; self-invocation and `final` methods skip validation, same rules as [[spring-transactional]].
- `MaxUploadSizeExceededException` is thrown before the controller is chosen, so a controller-local `@ExceptionHandler` does not see it; use a global advice.
- Tomcat has its own limit (`server.tomcat.max-swallow-size`, default 2MB): if the client sends more than that after a rejection, the connection is reset and the client sees a network error instead of 413.
- Related: [[spring-mvc-request-lifecycle]], [[rest-and-http]], [[annotations]] for how constraint annotations are declared.
