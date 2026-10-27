---
title: Валидация запросов и загрузка файлов в Spring MVC
tags: [spring-mvc, validation, bean-validation, multipart]
learned: 2026-10-22
---
## Кратко
Валидация использует **Bean Validation API** (Jakarta Validation 3, реализация — Hibernate Validator, подключается через `spring-boot-starter-validation`): поля DTO помечаются `@NotNull`, `@Size`, `@Pattern`, а на параметр контроллера ставится `@Valid`; ошибка превращается в `MethodArgumentNotValidException` → HTTP 400. `@Validated` добавляет группы валидации и валидацию на уровне методов в сервисах. **Загружаемые файлы** приходят как `multipart/form-data` и привязываются к параметрам `MultipartFile`; ограничения размера задаются свойствами.

## Основное
### Bean Validation
- Ограничения: `@NotNull`, `@NotBlank` (строки), `@NotEmpty` (коллекции), `@Size`, `@Min`/`@Max`, `@Positive`, `@Email`, `@Pattern`, `@Past`/`@Future`, `@Digits`. Чтобы валидация каскадно распространялась на вложенные объекты, на поле нужен `@Valid`; для коллекций: `List<@Valid Item>`.
- `@Valid` (Jakarta) на параметре `@RequestBody` / `@ModelAttribute` запускает валидацию при разрешении аргументов. Ошибка — `MethodArgumentNotValidException` (тело) или `BindException` (форма/query), обе `DefaultHandlerExceptionResolver` маппит в 400; с `spring.mvc.problemdetails.enabled=true` получается тело `ProblemDetail`.
- `@Validated` (Spring) — расширенная версия: поддерживает **группы** (`@Validated(OnCreate.class)`), а если поставить её на **класс**, включает валидацию методов через AOP-прокси ([[spring-aop]]), так что проверяются `@NotNull` на параметрах методов сервиса и `@Valid` на возвращаемых значениях; исключение — `ConstraintViolationException`. Начиная со Spring 6.1 параметры методов контроллера вроде `@RequestParam @Min(1) int page` валидируются без `@Validated` на классе.
- Собственное ограничение: аннотация с `@Constraint(validatedBy = ...)` и реализация `ConstraintValidator<Annotation, Type>`; валидаторы — Spring-бины, поэтому в них можно внедрить репозиторий для проверки уникальности.
- Собственный валидатор в стиле Spring: реализовать `org.springframework.validation.Validator` (`supports`, `validate(target, Errors)`) и зарегистрировать через `@InitBinder` в контроллере или вызвать вручную. Полезно для правил, затрагивающих несколько полей, которые неудобно выражать аннотациями.
- Records тоже работают: `record CreateEsimRequest(@NotBlank String msisdn, @Pattern(regexp = "\\d{19,20}") String iccid) {}`.

### Multipart-загрузка
- Клиент отправляет `Content-Type: multipart/form-data` (его должна выставить форма на фронтенде или `FormData`; не задавай его вручную с boundary в JS). `StandardServletMultipartResolver` в Spring разбирает запрос и отдаёт каждую часть как `MultipartFile` (`getOriginalFilename()`, `getContentType()`, `getSize()`, `getInputStream()`, `transferTo(Path)`).
- Привязка через `@RequestParam("file") MultipartFile file` или `@RequestPart` (второй вариант также десериализует JSON-часть рядом с файлом).
- Ограничения (свойства Boot): `spring.servlet.multipart.max-file-size=10MB` на файл, `spring.servlet.multipart.max-request-size=10MB` на весь запрос, `file-size-threshold` — порог, выше которого части пишутся на диск, `location` — временный каталог. При превышении бросается `MaxUploadSizeExceededException` (маппь его в 413).
- Большие файлы передавай потоком из `getInputStream()` в S3/MinIO вместо `getBytes()` и проверяй реальный тип контента (magic bytes), а не заголовок.

## Пример
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

## Подводные камни
- Забытый `@Valid` на параметре: ограничения объявлены, но их никто не проверяет. Также легко забыть `spring-boot-starter-validation` (с Boot 2.3 он больше не входит в `starter-web`).
- Для каскадной валидации нужен `@Valid` на вложенном поле; `@NotNull` на родителе не валидирует дочерние объекты.
- `@Validated` на классе сервиса создаёт прокси; самовызов и `final`-методы пропускают валидацию, правила те же, что в [[spring-transactional]].
- `MaxUploadSizeExceededException` бросается до выбора контроллера, поэтому локальный `@ExceptionHandler` контроллера его не видит; используй глобальный advice.
- У Tomcat есть собственный лимит (`server.tomcat.max-swallow-size`, по умолчанию 2MB): если после отказа клиент отправляет больше, соединение сбрасывается, и клиент видит сетевую ошибку вместо 413.
- Связанные заметки: [[spring-mvc-request-lifecycle]], [[rest-and-http]], [[annotations]] — как объявляются аннотации-ограничения.
