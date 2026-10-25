---
title: API documentation with OpenAPI and Swagger
tags: [openapi, swagger, springdoc, api-design]
learned: 2026-10-22
---
## TL;DR
**OpenAPI** is the specification (a YAML/JSON document describing endpoints, parameters, schemas, security). **Swagger** is the family of tools around it: Swagger UI (interactive docs in the browser), Swagger Editor, Codegen. In Spring Boot 3 the de facto library is **springdoc-openapi**: add one dependency and it generates the OpenAPI 3.1 document from your controllers, Bean Validation annotations and Spring Security config at runtime, with Swagger UI at `/swagger-ui.html`. The old `springfox` is dead (no Boot 3 support).

## Key points
### What it gives you
- A machine-readable contract that frontend, QA and partner teams can read, and that tools can generate clients from (`openapi-generator` for TypeScript, Java, Go).
- Interactive "try it out" UI for manual testing, including auth headers.
- Validation of the contract in CI (breaking-change detection with `openapi-diff`).
- Two approaches: **code-first** (annotate controllers, generate the spec; the Spring default) and **contract-first** (write `openapi.yaml`, generate server interfaces and DTOs with `openapi-generator-maven-plugin`, implement them). Contract-first fits multi-team or public APIs; code-first fits internal services.

### springdoc setup
- Dependency: `org.springdoc:springdoc-openapi-starter-webmvc-ui` (Boot 3 / Jakarta; use `-webflux-ui` for WebFlux). Version 2.x.
- Endpoints: `/v3/api-docs` (JSON), `/v3/api-docs.yaml`, `/swagger-ui.html`. Configure with `springdoc.api-docs.path`, `springdoc.swagger-ui.path`, `springdoc.packages-to-scan`, `springdoc.paths-to-match`, `springdoc.show-actuator`.
- Grouping: `GroupedOpenApi` beans split public vs internal APIs into separate documents.
- Annotations (`io.swagger.v3.oas.annotations`): `@Tag` on the controller, `@Operation(summary, description)`, `@ApiResponse(responseCode, description, content)`, `@Parameter`, `@Schema(description, example)` on DTO fields, `@SecurityRequirement`. Bean Validation (`@NotNull`, `@Size`, `@Pattern`) is picked up automatically as schema constraints ([[spring-mvc-validation-and-uploads]]).
- A global `OpenAPI` bean sets title, version, servers and the security scheme (bearer JWT), so Swagger UI gets an "Authorize" button.
- Hide an endpoint with `@Hidden` or `springdoc.show-actuator=false`.
- In Spring Security the docs endpoints must be permitted (`/v3/api-docs/**`, `/swagger-ui/**`, `/swagger-ui.html`), and in prod usually disabled altogether (`springdoc.api-docs.enabled=false`) or restricted to an internal network.

## Example
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

## Pitfalls
- `springfox-swagger2` / `springfox-boot-starter` does not work on Boot 3 (javax, incompatible path matching); migrate to springdoc.
- Returning `Object`, `Map` or a JPA entity gives useless or leaking schemas; return DTOs/records so the schema is accurate.
- Documented responses drift from reality when written by hand in `@ApiResponse`; keep them minimal and let `@ControllerAdvice` + `ProblemDetail` define the error shape once.
- Swagger UI exposed publicly on a production service is an information leak and a target; disable or protect it ([[spring-security-basics]]).
- Generated OpenAPI for file uploads needs `@RequestPart` with explicit `@Schema(type = "string", format = "binary")` on some versions.
- Related: [[rest-and-http]], [[spring-mvc-request-lifecycle]], [[rest-clients]] (generating a client from a partner's spec).
