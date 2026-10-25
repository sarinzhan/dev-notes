---
title: Calling external APIs: RestTemplate, RestClient, WebClient, Feign and how to test them
tags: [rest-client, http, resttemplate, webclient, wiremock, testing]
learned: 2026-10-22
---
## TL;DR
Spring offers three HTTP clients: **`RestTemplate`** (classic, synchronous, template-method API, in maintenance mode), **`RestClient`** (Spring 6.1 / Boot 3.2, synchronous with a fluent API, the recommended default), and **`WebClient`** (reactive, non-blocking, from WebFlux). On top of any of them, **HTTP interface clients** (`@HttpExchange`, Spring 6) or **OpenFeign** give declarative interfaces. Testing: `MockRestServiceServer` for `RestTemplate`/`RestClient` unit tests, **WireMock** for a realistic fake server with any client, Mockito for pure unit tests of the code around the client.

## Key points
### The clients
| Client | Model | Status | Notes |
|---|---|---|---|
| `RestTemplate` | sync, blocking | maintenance since 5.0, not deprecated | `getForObject`, `exchange(...)`; configure via `RestTemplateBuilder`; many overloads, hard to read |
| `RestClient` | sync, blocking, fluent | Spring 6.1+, recommended | same `HttpMessageConverter`s and `ClientHttpRequestFactory` as `RestTemplate`; `RestClient.create(restTemplate)` reuses config; `.retrieve().body(T)` / `.toEntity(T)` |
| `WebClient` | reactive, non-blocking | WebFlux | returns `Mono`/`Flux`; `.block()` makes it sync but wastes the point; see [[spring-reactive-webclient-sse]] |
| HTTP interface (`@HttpExchange`) | declarative over any of the above | Spring 6.0+ | `HttpServiceProxyFactory` builds a proxy; Boot 3.4 can auto-register them |
| OpenFeign | declarative | Spring Cloud | `@FeignClient`, integrates with load balancing and circuit breakers; extra dependency |

- Under the hood: `ClientHttpRequestFactory` chooses the transport: `JdkClientHttpRequestFactory` (Java 11 `HttpClient`, HTTP/2 capable), Apache HttpClient 5 (connection pool, the usual production choice), `SimpleClientHttpRequestFactory` (`HttpURLConnection`, no pooling, avoid). Boot's `RestClient.Builder` picks the best one on the classpath.
- Always set **connect and read timeouts** (`ClientHttpRequestFactorySettings` in Boot 3.x, or `HttpComponentsClientHttpRequestFactory`). Defaults are infinite or very long; a hung downstream then exhausts your Tomcat threads.
- **Connection pooling**: with HTTP/1.1, each in-flight request needs its own TCP connection; keep-alive lets the pool reuse them (`PoolingHttpClientConnectionManager`, `setMaxTotal`, `setDefaultMaxPerRoute`, default per-route is only 5). HTTP/2 multiplexes requests on one connection. The server-side counterpart is in [[spring-boot-properties-and-profiles]].
- **Compression**: send `Accept-Encoding: gzip`; Apache and JDK clients decompress transparently.
- Error handling: `retrieve()` throws `HttpClientErrorException` / `HttpServerErrorException` (`RestClientResponseException`) on 4xx/5xx; customize with `.onStatus(...)`. For resilience wrap calls with Resilience4j (`@Retry`, `@CircuitBreaker`, `@TimeLimiter`), and remember an HTTP call inside a DB transaction holds the connection ([[spring-transactional]]).
- Blocking clients on Java 21 scale well with virtual threads (`spring.threads.virtual.enabled=true`, [[virtual-threads]]); you do not need WebClient just for throughput.
- Observability: Boot's `RestClient.Builder` and `RestTemplateBuilder` are instrumented with Micrometer (`http.client.requests` metrics, trace propagation) when you use the injected builder instead of `new`.

### Testing external clients
| Approach | Scope | Works with | Comments |
|---|---|---|---|
| Mockito | unit | any client (mock your own gateway class) | simplest; does not test serialization, URLs or status handling |
| `MockRestServiceServer` | unit / slice | `RestTemplate`, `RestClient` | Spring Test built-in; binds to the client, no server, fast; `expect(requestTo(...)).andRespond(withSuccess(...))`; `@RestClientTest` slice wires it; **not** for WebClient or Feign |
| WireMock | integration | any HTTP client | standalone or embedded stub server; verifies requests, injects delays, returns faults, records real traffic; a bit heavier, needs a port; `wiremock-spring-boot` or `@AutoConfigureWireMock` |
| `WebTestClient` | integration | WebFlux / any server | tests *your* server, not an outgoing client |
| `@MockBean`/`@MockitoBean` on a Feign client | unit / integration | Feign | basic stubbing of the interface |
| Spring Cloud Contract | integration | Feign, RestTemplate | consumer-driven contracts, stub runner |
| MockServer + Testcontainers | integration | any | full HTTP simulation in Docker; closest to prod |

Rule of thumb: Mockito around the gateway for business logic tests, `MockRestServiceServer` for the gateway's own mapping logic, WireMock for the integration test of the whole slice including timeouts and error statuses.

## Example
```java
@Component
public class VendorEsimGateway {
    private final RestClient client;

    VendorEsimGateway(RestClient.Builder builder, EsimVendorProps props) {
        this.client = builder.baseUrl(props.baseUrl())
                .defaultHeader(HttpHeaders.AUTHORIZATION, "Bearer " + props.apiKey())
                .requestFactory(ClientHttpRequestFactories.get(ClientHttpRequestFactorySettings.DEFAULTS
                        .withConnectTimeout(Duration.ofSeconds(2))
                        .withReadTimeout(props.timeout())))
                .build();
    }

    public ProfileStatus status(String iccid) {
        return client.get().uri("/profiles/{iccid}", iccid)
                .retrieve()
                .onStatus(HttpStatusCode::is4xxClientError,
                          (req, res) -> { throw new VendorProfileNotFound(iccid); })
                .body(ProfileStatus.class);
    }
}

@RestClientTest(VendorEsimGateway.class)
@EnableConfigurationProperties(EsimVendorProps.class)
class VendorEsimGatewayTest {
    @Autowired MockRestServiceServer server;
    @Autowired VendorEsimGateway gateway;

    @Test
    void mapsStatus() {
        server.expect(requestTo("https://sandbox.vendor.example/profiles/8999600000000000001"))
              .andExpect(method(HttpMethod.GET))
              .andRespond(withSuccess("""{"state":"RELEASED"}""", MediaType.APPLICATION_JSON));
        assertThat(gateway.status("8999600000000000001").state()).isEqualTo("RELEASED");
        server.verify();
    }
}
```

## Pitfalls
- `new RestTemplate()` / `RestClient.create()` in a field: no timeouts, no pooling, no metrics. Inject the Boot builder and build one bean per downstream.
- Reading the body twice (for logging and for parsing) fails because the stream is consumed; use a `BufferingClientHttpRequestFactory` or an interceptor that buffers.
- Retrying a non-idempotent POST without an idempotency key duplicates orders ([[idempotency]]).
- `@RestClientTest` only loads the listed component; if the gateway needs `@ConfigurationProperties` or Jackson customizations, import them explicitly.
- WireMock stubs that are too loose (`anyUrl()`) pass tests while the real URL is wrong; assert the path and body.
- Related: [[rest-and-http]], [[spring-reactive-webclient-sse]], [[testing-types-and-dev-cycles]], [[completable-future]] for fanning out several calls in parallel.
