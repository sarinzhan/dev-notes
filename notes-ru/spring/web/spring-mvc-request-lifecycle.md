---
title: Spring MVC: DispatcherServlet и жизненный цикл запроса
tags: [spring-mvc, web, dispatcher-servlet, controllers]
learned: 2026-10-22
---
## Кратко
Spring MVC — веб-модуль Spring Framework на основе сервлетов, построенный по паттерну **Model-View-Controller**. Один сервлет, **`DispatcherServlet`** (front controller), принимает каждый запрос и делегирует работу: `HandlerMapping` находит метод контроллера, `HandlerAdapter` вызывает его с аргументами, извлечёнными из запроса, возвращаемое значение записывает `HttpMessageConverter` (JSON для REST) или рендерит представление, а `HandlerExceptionResolver` превращают исключения в ответы. Интерцепторы и сервлетные фильтры оборачивают весь процесс.

## Основное
### Роли в MVC
- **Model**: данные и бизнес-логика (на практике: сервисы, сущности, DTO).
- **View**: отображает модель (шаблон Thymeleaf, а для REST эту роль играет JSON-тело).
- **Controller**: обрабатывает запрос пользователя и возвращает модель/представление или тело.

### Аннотации на классах
- `@Controller`: регистрирует класс как обработчик запросов; методы возвращают имена представлений, если не помечены `@ResponseBody`.
- `@RestController` = `@Controller` + `@ResponseBody`: возвращаемое значение каждого метода сразу идёт в тело ответа как JSON/XML, без разрешения представлений.
- `@RequestMapping("/api/v1/orders")` на классе задаёт общий префикс URL (а также тип контента, метод).

### Аннотации на методах-обработчиках
- `@RequestMapping` и сокращения `@GetMapping`, `@PostMapping`, `@PutMapping`, `@PatchMapping`, `@DeleteMapping`.
- `@PathVariable`: значение из пути URL (`/orders/{id}`), идентифицирует ресурс.
- `@RequestParam`: параметр строки запроса или формы (`?page=2`); `required`, `defaultValue`.
- `@RequestBody`: десериализует тело запроса в параметр (по умолчанию через Jackson).
- `@ResponseBody`: сериализует возвращаемое значение в тело, минуя представления.
- `@RequestHeader`, `@CookieValue`, `@ModelAttribute` (привязка формы), `Pageable`/`Sort` со Spring Data.
- `@Valid` / `@Validated` запускают Bean Validation для привязанного объекта ([[spring-mvc-validation-and-uploads]]).
- Возвращаемые типы: DTO (200 с телом), `ResponseEntity<T>`, когда нужно управлять статусом и заголовками, `void` с `@ResponseStatus`, `ProblemDetail` (Spring 6) для ошибок по RFC 9457.

### Жизненный цикл запроса по шагам
1. Первыми выполняются **сервлетные фильтры** (`FilterChain`): цепочка фильтров Spring Security ([[spring-security-basics]]), `CharacterEncodingFilter`, CORS, логирование запросов. Они видят «сырой» `HttpServletRequest`.
2. Запрос доходит до **`DispatcherServlet`** (замаплен на `/` через `DispatcherServletAutoConfiguration` в Boot).
3. **`HandlerMapping`** (`RequestMappingHandlerMapping`) сопоставляет путь, метод, заголовки и media types с `HandlerMethod` и возвращает `HandlerExecutionChain` = обработчик + интерцепторы.
4. **`preHandle` интерцепторов** (`HandlerInterceptor`): может прервать обработку, вернув `false`. Типичное применение: проверки авторизации, MDC/идентификаторы трассировки, ограничение частоты запросов (rate limiting).
5. **`HandlerAdapter`** (`RequestMappingHandlerAdapter`) адаптирует общий запрос к методу контроллера: `HandlerMethodArgumentResolver` собирают аргументы (`@PathVariable`, `@RequestBody` через `HttpMessageConverter`, `Principal`...), выполняется валидация, затем вызывается метод.
6. **Бизнес-логика**: контроллер → сервис → репозиторий.
7. **Формирование ответа**: `HandlerMethodReturnValueHandler` выбирает путь. Для `@ResponseBody` `HttpMessageConverter`, выбранный согласованием контента (заголовок `Accept`), пишет JSON (`MappingJackson2HttpMessageConverter`). Для имени представления `ViewResolver` рендерит шаблон.
8. **`postHandle` интерцепторов** (до рендеринга) и `afterCompletion` (всегда, даже при исключении).
9. **Обработка исключений**: исключение из шагов 5-7 проходит через `HandlerExceptionResolver`. `ExceptionHandlerExceptionResolver` ищет методы `@ExceptionHandler` в контроллере или в `@ControllerAdvice` / `@RestControllerAdvice`; `ResponseStatusExceptionResolver` обрабатывает `@ResponseStatus` и `ResponseStatusException`; `DefaultHandlerExceptionResolver` маппит исключения фреймворка (400 для некорректного тела, 405, 415). Включи `spring.mvc.problemdetails.enabled=true`, чтобы получать тела по RFC 9457 из коробки.
10. Ответ отправляется клиенту; фильтры раскручиваются в обратном порядке.

Фильтры и интерцепторы: фильтр — понятие Servlet API, выполняется для всего (включая статические ресурсы) и может подменять объекты запроса/ответа; интерцептор знает о Spring, выполняется только для запросов, сопоставленных с обработчиком, и знает, какой обработчик выбран.

### Потоки
Каждый запрос выполняется в одном рабочем потоке Tomcat ([[executors-and-thread-pools]]); блокирующий ввод-вывод внутри обработчика блокирует этот поток. Возврат `Callable`, `DeferredResult`, `CompletableFuture` или `SseEmitter` освобождает поток Tomcat (асинхронный сервлет); в Java 21 `spring.threads.virtual.enabled=true`, наоборот, делает блокировку дешёвой ([[virtual-threads]]). Полностью неблокирующая альтернатива — WebFlux ([[spring-reactive-webclient-sse]]).

## Пример
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

## Подводные камни
- Исключения, брошенные в **фильтре** (например, в JWT-фильтре), никогда не доходят до `@ControllerAdvice`; обрабатывай их в самом фильтре или через `AuthenticationEntryPoint` в Spring Security ([[jwt-authentication]]).
- `@ControllerAdvice` с `@ExceptionHandler(Exception.class)`, который на всё возвращает 500, скрывает `MethodArgumentNotValidException` (должно быть 400); упорядочивай обработчики от конкретных к общим.
- Возврат JPA-сущности из контроллера сериализует ленивые коллекции после закрытия транзакции (`LazyInitializationException`) или раскрывает весь граф; возвращай DTO ([[jpa-n-plus-one]]).
- Сопоставление с завершающим слэшем отключено в Spring 6: `/orders/` больше не совпадает с `/orders`.
- Метод `@RestController` без `@ResponseStatus` возвращает 200 даже при создании; где это важно, используй 201 и `Location` ([[rest-and-http]]).
- Связанные заметки: [[rest-and-http]], [[spring-mvc-validation-and-uploads]], [[openapi-swagger]].
