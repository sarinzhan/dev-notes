---
title: Spring MVC: DispatcherServlet and the request lifecycle
tags: [spring-mvc, web, dispatcher-servlet, controllers]
learned: 2026-10-22
---
## TL;DR
Spring MVC is the servlet-based web module of Spring Framework built on the **Model-View-Controller** pattern. One servlet, the **`DispatcherServlet`** (front controller), receives every request and delegates: `HandlerMapping` finds the controller method, `HandlerAdapter` invokes it with arguments resolved from the request, the return value is written by an `HttpMessageConverter` (JSON for REST) or rendered by a view, and `HandlerExceptionResolver`s turn exceptions into responses. Interceptors and servlet filters wrap the whole thing.

## Key points
### MVC roles
- **Model**: the data and business logic (in practice: services, entities, DTOs).
- **View**: renders the model (Thymeleaf template, or for REST the JSON body plays this role).
- **Controller**: handles the user request and returns a model/view or a body.

### Annotations on classes
- `@Controller`: registers the class as a request handler; methods return view names unless annotated with `@ResponseBody`.
- `@RestController` = `@Controller` + `@ResponseBody`: every method's return value goes straight into the response body as JSON/XML, no view resolution.
- `@RequestMapping("/api/v1/orders")` on the class sets the common URL prefix (also content type, method).

### Annotations on handler methods
- `@RequestMapping` and the shortcuts `@GetMapping`, `@PostMapping`, `@PutMapping`, `@PatchMapping`, `@DeleteMapping`.
- `@PathVariable`: a value from the URL path (`/orders/{id}`), identifies the resource.
- `@RequestParam`: a query-string or form parameter (`?page=2`); `required`, `defaultValue`.
- `@RequestBody`: deserializes the request body into a parameter (Jackson by default).
- `@ResponseBody`: serialize the return value into the body, skipping views.
- `@RequestHeader`, `@CookieValue`, `@ModelAttribute` (form binding), `Pageable`/`Sort` with Spring Data.
- `@Valid` / `@Validated` trigger Bean Validation on the bound object ([[spring-mvc-validation-and-uploads]]).
- Return types: a DTO (200 with body), `ResponseEntity<T>` when you need to control status and headers, `void` with `@ResponseStatus`, `ProblemDetail` (Spring 6) for RFC 9457 errors.

### Request lifecycle, step by step
1. **Servlet filters** run first (`FilterChain`): Spring Security's filter chain ([[spring-security-basics]]), `CharacterEncodingFilter`, CORS, request logging. They see the raw `HttpServletRequest`.
2. The request reaches **`DispatcherServlet`** (mapped to `/` by Boot's `DispatcherServletAutoConfiguration`).
3. **`HandlerMapping`** (`RequestMappingHandlerMapping`) matches path, method, headers and media types to a `HandlerMethod`, and returns a `HandlerExecutionChain` = handler + interceptors.
4. **Interceptors' `preHandle`** (`HandlerInterceptor`): can short-circuit by returning `false`. Typical uses: auth checks, MDC/trace ids, rate limiting.
5. **`HandlerAdapter`** (`RequestMappingHandlerAdapter`) adapts the generic request to the controller method: `HandlerMethodArgumentResolver`s build the arguments (`@PathVariable`, `@RequestBody` via `HttpMessageConverter`, `Principal`...), validation runs, then the method is invoked.
6. **Business logic** in the controller → service → repository.
7. **Response forming**: `HandlerMethodReturnValueHandler` picks a path. For `@ResponseBody` the `HttpMessageConverter` chosen by content negotiation (`Accept` header) writes JSON (`MappingJackson2HttpMessageConverter`). For a view name a `ViewResolver` renders a template.
8. **Interceptors' `postHandle`** (before rendering) and `afterCompletion` (always, even on exception).
9. **Exception handling**: an exception from steps 5-7 goes through `HandlerExceptionResolver`s. `ExceptionHandlerExceptionResolver` finds `@ExceptionHandler` methods in the controller or a `@ControllerAdvice` / `@RestControllerAdvice`; `ResponseStatusExceptionResolver` handles `@ResponseStatus` and `ResponseStatusException`; `DefaultHandlerExceptionResolver` maps framework exceptions (400 for bad body, 405, 415). Enable `spring.mvc.problemdetails.enabled=true` for RFC 9457 bodies out of the box.
10. The response is flushed to the client; filters unwind.

Filters vs interceptors: a filter is a Servlet API concept, runs for everything (static resources too) and can replace the request/response objects; an interceptor is Spring-aware, runs only for handler-mapped requests and knows which handler was chosen.

### Threading
Each request runs on one Tomcat worker thread ([[executors-and-thread-pools]]); blocking I/O inside the handler blocks that thread. Returning `Callable`, `DeferredResult`, `CompletableFuture` or `SseEmitter` releases the Tomcat thread (async servlet); with Java 21 `spring.threads.virtual.enabled=true` makes blocking cheap instead ([[virtual-threads]]). The fully non-blocking alternative is WebFlux ([[spring-reactive-webclient-sse]]).

## Example
```java
@RestController
@RequestMapping("/api/v1/esims")
public class EsimController {

    private final EsimService service;
    EsimController(EsimService service) { this.service = service; }

    @GetMapping("/{iccid}")
    public EsimDto get(@PathVariable String iccid) {
        return service.find(iccid).orElseThrow(() -> new EsimNotFoundException(iccid));
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public EsimDto create(@Valid @RequestBody CreateEsimRequest req,
                          @RequestParam(defaultValue = "false") boolean activateNow) {
        return service.create(req, activateNow);
    }
}

@RestControllerAdvice
class ApiErrors {
    @ExceptionHandler(EsimNotFoundException.class)
    ProblemDetail notFound(EsimNotFoundException e) {
        var pd = ProblemDetail.forStatusAndDetail(HttpStatus.NOT_FOUND, e.getMessage());
        pd.setProperty("iccid", e.iccid());
        return pd;
    }
}
```

## Pitfalls
- Exceptions thrown in a **filter** (e.g. a JWT filter) never reach `@ControllerAdvice`; handle them in the filter or via Spring Security's `AuthenticationEntryPoint` ([[jwt-authentication]]).
- A `@ControllerAdvice` with `@ExceptionHandler(Exception.class)` that returns 500 for everything hides `MethodArgumentNotValidException` (should be 400); order handlers from specific to generic.
- Returning a JPA entity from a controller serializes lazy collections after the transaction closed (`LazyInitializationException`) or leaks the whole graph; return DTOs ([[jpa-n-plus-one]]).
- Trailing-slash matching was turned off in Spring 6: `/orders/` no longer matches `/orders`.
- A `@RestController` method without `@ResponseStatus` returns 200 even for creates; use 201 and `Location` where it matters ([[rest-and-http]]).
- Related: [[rest-and-http]], [[spring-mvc-validation-and-uploads]], [[openapi-swagger]].
