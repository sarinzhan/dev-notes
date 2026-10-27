---
title: mTLS and service-to-service authentication
tags: [networking, mtls, service-auth, spiffe, zero-trust]
learned: 2026-10-27
---
## TL;DR
Mutual TLS adds a client certificate to the handshake, so both sides prove identity before any request is sent; the service's identity becomes "whoever holds the key for this certificate" instead of "whoever knows this API key". It is the strongest and most operationally expensive option: certificates must be issued, rotated and revoked per workload, which is why in practice mTLS is almost always done by a sidecar or a platform (Istio/Linkerd with SPIFFE identities, cloud load balancers) rather than by Spring code. For a handful of services, a signed token or an API key over normal TLS is still a defensible choice if it is scoped, rotated and never logged.

## Key points
### How mTLS differs from TLS
- Normal TLS ([[tls-handshake-and-certificates]]): the server presents a certificate, the client verifies it; the client is anonymous at the transport layer.
- mTLS: the server sends a `CertificateRequest` in the handshake; the client answers with its own `Certificate` and a `CertificateVerify` signature. The server validates the chain against its **client trust store** and can then read the subject/SAN as the caller's identity.
- Failure mode: server requires a client certificate, client has none → `handshake_failure` or `bad_certificate` alert before any HTTP. Hard to debug from HTTP logs because there are none.
- Identity is in the certificate SAN, usually a URI such as `spiffe://cluster.local/ns/esim/sa/provisioning-api`; the application maps that to roles.

### Options compared
| Method | Proves | Rotation | Where it breaks | Good for |
|---|---|---|---|---|
| mTLS | possession of a private key, per workload | automated by mesh/cert-manager (hours to days) | corporate TLS inspection, L7 proxies that terminate TLS, client libs without client-cert support | zero-trust internal networks, partner APIs with contracts, regulated traffic |
| Signed JWT (client credentials, OAuth2) | holder of a token issued by an IdP ([[authentication-vs-authorization]]) | minutes (token TTL), keys at the IdP | IdP outage blocks all calls; token replay within TTL | service-to-service through gateways, mixed infra |
| API key / shared secret | knowledge of a string | manual, painful | leaked in logs, repos, headers; no identity granularity | low-risk integrations, webhooks (as HMAC, see [[api-security-checklist]]) |
| Network only (VPC, security group) | being on the network | n/a | lateral movement after one compromise | defence in depth, never alone |
| Mesh (Istio/Linkerd) mTLS | SPIFFE identity, enforced in sidecar/ambient proxy | fully automated | debugging through sidecars, extra hop latency | Kubernetes fleets where app code should not know about certificates |

### SPIFFE / SPIRE idea
- SPIFFE standardises a workload identity: a URI SAN `spiffe://trust-domain/path` inside a short-lived X.509 (SVID) issued by a local agent that attests the workload (which pod, which service account). No secret is ever copied by hand; the agent delivers and rotates the certificate over a Unix socket.
- Service meshes implement this internally; you write authorization policy ("`payments` may call `ledger` on `POST /entries`") and the proxies enforce it per connection and per request.

### Doing it in Spring
- Server side: `server.ssl.client-auth=need` (or `want` for optional), `server.ssl.trust-store` with the client CA; Spring Security's `x509()` login extracts the subject and maps it to a `UserDetails`/authorities ([[spring-security-basics]]).
- Client side: a key store with the client certificate and key, delivered as a Spring Boot 3.1+ SSL bundle to `RestClient`, `WebClient`, Kafka (`ssl.keystore.*`), Postgres (`sslcert`/`sslkey`, or `sslmode=verify-full` server-only).
- Bundles support `reload-on-update`, so cert-manager can rotate the files and the JVM picks them up without restart.

### Where to terminate
- At a reverse proxy or gateway ([[reverse-proxy-nginx-basics]], [[api-gateway-and-service-discovery]]): nginx `ssl_verify_client on` validates, then forwards the subject in a header (`X-SSL-Client-S-DN`); the app must trust that header only from the proxy.
- At the application: needed when the proxy is not trusted or when a TLS-terminating load balancer cannot do client auth (many L7 cloud balancers cannot; an L4 passthrough can, see [[load-balancers-l4-vs-l7]]).

## Example
A Spring Boot 3 service that both requires client certificates from callers and presents its own when calling the eSIM vendor, all configured through SSL bundles. The certificate's SAN identifies the caller, Spring Security maps it to a role.
```yaml
spring:
  ssl:
    bundle:
      pem:
        server:                       # our identity + which client CA we trust
          reload-on-update: true
          keystore:
            certificate: "file:/etc/tls/tls.crt"
            private-key: "file:/etc/tls/tls.key"
          truststore:
            certificate: "file:/etc/tls/client-ca.crt"
        vendor-client:                # identity we present to the SM-DP+ vendor
          keystore:
            certificate: "file:/etc/tls/vendor/client.crt"
            private-key: "file:/etc/tls/vendor/client.key"
          truststore:
            certificate: "file:/etc/tls/vendor/vendor-ca.crt"
server:
  port: 8443
  ssl:
    bundle: server
    client-auth: need                 # handshake fails without a trusted client cert
```
```java
@Configuration
public class MtlsSecurityConfig {

    @Bean
    SecurityFilterChain chain(HttpSecurity http) throws Exception {
        return http
            .x509(x -> x.subjectPrincipalRegex("CN=(.*?)(?:,|$)")   // CN of the client cert -> principal
                        .userDetailsService(cn -> User.withUsername(cn)
                                .password("")                        // not used: the TLS handshake already authenticated
                                .roles(cn.equals("billing-api") ? "BILLING" : "SERVICE")
                                .build()))
            .authorizeHttpRequests(a -> a
                .requestMatchers(HttpMethod.POST, "/api/v1/esims/*/activations").hasRole("BILLING")
                .anyRequest().hasRole("SERVICE"))
            .sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
            .csrf(AbstractHttpConfigurer::disable)
            .build();
    }

    @Bean
    RestClient vendorClient(RestClient.Builder builder, RestClientSsl ssl) {
        return builder.baseUrl("https://smdp.vendor.example")
                .apply(ssl.fromBundle("vendor-client"))
                .build();
    }
}
```
Verifying from the shell, which is also how you prove to the vendor that the problem is on their side:
```bash
$ curl -sS --cert client.crt --key client.key --cacert vendor-ca.crt https://smdp.vendor.example/es2plus/ping
{"status":"ok"}
$ curl -sS --cacert vendor-ca.crt https://smdp.vendor.example/es2plus/ping     # no client cert
curl: (56) OpenSSL SSL_read: tlsv13 alert certificate required
```

## Pitfalls
- Trusting a `X-SSL-Client-*` header from anywhere: if the app port is reachable without the proxy, anyone can forge the identity. Bind the app to the private network and verify the proxy's own certificate or source.
- Long-lived client certificates copied into repos and images; a leaked client cert is an API key with extra steps. Rotate in days, not years, and automate.
- Revocation does not really work (CRLs stale, OCSP ignored); short lifetimes are the actual revocation mechanism.
- Corporate TLS-inspecting proxies cannot forward client certificates; mTLS to an external partner from an office network needs an exemption on the firewall ([[tls-handshake-and-certificates]]).
- Mixing identity and authorization: mTLS says who is calling, not what they may do; still write the authorization rules.
- Related: [[authentication-vs-authorization]] for the token-based alternatives, [[secrets-management]] for where private keys should live.
