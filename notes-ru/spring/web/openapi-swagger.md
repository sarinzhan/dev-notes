---
title: Документация API с OpenAPI и Swagger
tags: [openapi, swagger, springdoc, api-design]
learned: 2026-10-22
---
## Кратко
**OpenAPI** — это спецификация (документ YAML/JSON, который описывает эндпоинты, параметры, схемы, безопасность). **Swagger** — семейство инструментов вокруг неё: Swagger UI (интерактивная документация в браузере), Swagger Editor, Codegen. В Spring Boot 3 стандартом де-факто стала библиотека **springdoc-openapi**: достаточно одной зависимости, и она во время работы приложения генерирует документ OpenAPI 3.1 по контроллерам, аннотациям Bean Validation и конфигурации Spring Security, а Swagger UI доступен по `/swagger-ui.html`. Старый `springfox` мёртв (Boot 3 не поддерживает).

## Основное
### Что это даёт
- Машиночитаемый контракт, который читают фронтенд, QA и команды партнёров и по которому инструменты генерируют клиентов (`openapi-generator` для TypeScript, Java, Go).
- Интерактивный UI «try it out» для ручного тестирования, включая заголовки авторизации.
- Проверку контракта в CI (обнаружение ломающих изменений через `openapi-diff`).
- Два подхода: **code-first** (аннотировать контроллеры и генерировать спецификацию; по умолчанию в Spring) и **contract-first** (написать `openapi.yaml`, сгенерировать серверные интерфейсы и DTO через `openapi-generator-maven-plugin` и реализовать их). Contract-first подходит для API нескольких команд или публичных API, code-first — для внутренних сервисов.

### Настройка springdoc
- Зависимость: `org.springdoc:springdoc-openapi-starter-webmvc-ui` (Boot 3 / Jakarta; для WebFlux — `-webflux-ui`). Версия 2.x.
- Эндпоинты: `/v3/api-docs` (JSON), `/v3/api-docs.yaml`, `/swagger-ui.html`. Настраиваются через `springdoc.api-docs.path`, `springdoc.swagger-ui.path`, `springdoc.packages-to-scan`, `springdoc.paths-to-match`, `springdoc.show-actuator`.
- Группировка: бины `GroupedOpenApi` разделяют публичные и внутренние API на отдельные документы.
- Аннотации (`io.swagger.v3.oas.annotations`): `@Tag` на контроллере, `@Operation(summary, description)`, `@ApiResponse(responseCode, description, content)`, `@Parameter`, `@Schema(description, example)` на полях DTO, `@SecurityRequirement`. Bean Validation (`@NotNull`, `@Size`, `@Pattern`) подхватывается автоматически как ограничения схемы ([[spring-mvc-validation-and-uploads]]).
- Глобальный бин `OpenAPI` задаёт title, version, servers и схему безопасности (bearer JWT), так что в Swagger UI появляется кнопка «Authorize».
- Скрыть эндпоинт можно через `@Hidden` или `springdoc.show-actuator=false`.
- В Spring Security эндпоинты документации нужно разрешить (`/v3/api-docs/**`, `/swagger-ui/**`, `/swagger-ui.html`), а в prod их обычно отключают полностью (`springdoc.api-docs.enabled=false`) или открывают только во внутренней сети.

## Пример
```java
@Configuration
public class OpenApiConfig {
    @Bean
    OpenAPI esimApi() {
        return new OpenAPI()
            .info(new Info().title("eSIM Provisioning API").version("v1")
                  .description("Internal API for eSIM profile lifecycle"))
            .components(new Components().addSecuritySchemes("bearer",
                  new SecurityScheme().type(SecurityScheme.Type.HTTP).scheme("bearer").bearerFormat("JWT")))
            .addSecurityItem(new SecurityRequirement().addList("bearer"));
    }
}

@RestController
@RequestMapping("/api/v1/esims")
@Tag(name = "eSIM profiles")
public class EsimController {

    @Operation(summary = "Get profile by ICCID")
    @ApiResponse(responseCode = "200", description = "Profile found")
    @ApiResponse(responseCode = "404", description = "Unknown ICCID",
                 content = @Content(schema = @Schema(implementation = ProblemDetail.class)))
    @GetMapping("/{iccid}")
    public EsimDto get(@Parameter(description = "19-20 digit ICCID", example = "8999600000000000001")
                       @PathVariable String iccid) {
        return service.get(iccid);
    }
}
```
```yaml
springdoc:
  api-docs:
    path: /v3/api-docs
  swagger-ui:
    path: /docs
    operations-sorter: method
  packages-to-scan: kg.beeline.esim.api
```

## Подводные камни
- `springfox-swagger2` / `springfox-boot-starter` не работает на Boot 3 (javax, несовместимый path matching); нужно мигрировать на springdoc.
- Если возвращать `Object`, `Map` или JPA-сущность, схемы получаются бесполезными или раскрывают лишнее; возвращай DTO/records, чтобы схема была точной.
- Ответы, описанные вручную в `@ApiResponse`, со временем расходятся с реальностью; держи их минимальными, а форму ошибки определяй один раз через `@ControllerAdvice` + `ProblemDetail`.
- Swagger UI, открытый публично на production-сервисе, — это утечка информации и мишень для атак; отключи или защити его ([[spring-security-basics]]).
- Для загрузки файлов сгенерированному OpenAPI в некоторых версиях нужен `@RequestPart` с явным `@Schema(type = "string", format = "binary")`.
- Связанные заметки: [[rest-and-http]], [[spring-mvc-request-lifecycle]], [[rest-clients]] (генерация клиента по спецификации партнёра).
