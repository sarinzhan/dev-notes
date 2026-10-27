---
title: gRPC and Protocol Buffers
tags: [networking, grpc, protobuf, http2, rpc]
learned: 2026-10-27
---
## TL;DR
gRPC is a binary RPC framework: you describe services and messages in a `.proto` file, the compiler generates client stubs and server skeletons for every language, and calls travel as Protobuf bytes over HTTP/2 streams. Compared with JSON over REST it gives smaller payloads, typed contracts, four call shapes (unary, server-streaming, client-streaming, bidirectional) and built-in deadlines, at the cost of browser unfriendliness, harder debugging and L7 infrastructure that must understand HTTP/2. Use it for internal service-to-service and streaming APIs; keep REST for public and browser-facing ones.

## Key points
### Protocol Buffers
- Schema-first IDL (`proto3`): messages with numbered fields; the **field number**, not the name, is what goes on the wire, so renaming is free and the number is forever.
- Encoding: tag (field number + wire type) then value, varint-compressed integers, no field names, no whitespace. A typical message is 3–10× smaller than JSON and parses without reflection.
- Scalars have defaults (`0`, `""`, `false`) and are not serialised when default; unset and default are indistinguishable unless you use `optional` (proto3 since 3.15) or wrapper types.
- Schema evolution rules: add fields with new numbers (old readers ignore them, new readers see defaults); never reuse or renumber; mark removed numbers `reserved`; do not change a field's type. Same compatibility story as Avro in Kafka ([[message-schema-evolution]]).
- Protobuf is also usable without gRPC: Kafka payloads with a schema registry, files, Redis values.

### gRPC on the wire
- Every call is an HTTP/2 stream: `POST /package.Service/Method`, `content-type: application/grpc`, body is length-prefixed Protobuf messages; status comes in trailers (`grpc-status`, `grpc-message`), which is why HTTP/1.1 proxies cannot carry it ([[http-1-1-vs-2-vs-3]]).
- Call types: unary (request → response), server streaming (one request, many responses), client streaming, bidirectional. Streaming maps directly onto HTTP/2 flow control, so backpressure is built in ([[backpressure-and-flow-control]]).
- **Deadlines** propagate: the client sets `deadline = now + 2 s`, every hop sees the remaining budget and gives up early instead of doing useless work; the single biggest operational advantage over ad-hoc REST timeouts ([[failure-modes-and-timeouts]]).
- Metadata = headers (auth tokens, trace context) ([[distributed-tracing]]). Status codes are gRPC's own (`OK`, `NOT_FOUND`, `UNAVAILABLE`, `DEADLINE_EXCEEDED`, `RESOURCE_EXHAUSTED`...), mapped to HTTP 200 at the transport level, so an HTTP-level dashboard shows everything as success.
- Channel = one or more HTTP/2 connections with client-side load balancing (`round_robin` over resolved addresses) and reconnect/backoff. Interceptors on both sides for auth, retries, metrics.

### gRPC vs REST/JSON
| | gRPC | REST + JSON |
|---|---|---|
| Contract | `.proto`, enforced by codegen | OpenAPI, optional ([[openapi-swagger]]) |
| Payload | binary, compact | text, readable |
| Transport | HTTP/2 only | any HTTP |
| Streaming | native, both directions | SSE/WebSocket bolted on ([[websockets-sse-and-long-polling]]) |
| Browser | needs grpc-web proxy | native |
| Debugging | `grpcurl`, reflection, Wireshark dissector | `curl`, any log |
| Caching | none (POST, no HTTP semantics) | HTTP caches ([[http-caching]]) |
| Load balancing | per-stream needs L7/mesh; L4 pins all calls to one pod | per-request anywhere ([[load-balancers-l4-vs-l7]]) |
| Fits | internal APIs, polyglot teams, high QPS, streaming, mobile with strict bandwidth | public APIs, browsers, CRUD, anything a partner integrates with by hand |

### In the Java/Spring ecosystem
- `grpc-java` with the `protobuf-maven-plugin`; servers run on Netty. Spring integration via Spring gRPC (official since 2025, `spring-grpc-spring-boot-starter`) or the older `grpc-spring-boot-starter` community project. Service classes extend the generated `*ImplBase`; a `StreamObserver` is the callback for responses.
- Security: TLS on the channel, mTLS for service identity ([[mtls-and-service-to-service-auth]]), bearer tokens in metadata.
- Testing: in-process transport (`InProcessServerBuilder`) runs client and server in one JVM without sockets.

## Example
A provisioning-status API as it would look in gRPC, with the Java server side. Field numbers are the contract; `reserved` protects a removed field.
```protobuf
syntax = "proto3";
package esim.v1;
option java_package = "kg.beeline.esim.v1";
option java_multiple_files = true;

service Provisioning {
  rpc GetProfile (GetProfileRequest) returns (Profile);
  rpc WatchProfile (GetProfileRequest) returns (stream ProfileEvent);   // server streaming
}

message GetProfileRequest { string iccid = 1; }

message Profile {
  string iccid = 1;
  ProfileState state = 2;
  reserved 3;                       // was 'legacy_eid', removed in v1.4
  optional string eid = 4;          // optional: distinguishes unset from ""
}

enum ProfileState { PROFILE_STATE_UNSPECIFIED = 0; RELEASED = 1; DOWNLOADED = 2; INSTALLED = 3; }
message ProfileEvent { Profile profile = 1; int64 occurred_at_ms = 2; }
```
```java
@GrpcService   // Spring gRPC / grpc-spring-boot-starter
public class ProvisioningGrpcService extends ProvisioningGrpc.ProvisioningImplBase {
    private final ProfileRepository profiles;

    public ProvisioningGrpcService(ProfileRepository profiles) { this.profiles = profiles; }

    @Override
    public void getProfile(GetProfileRequest req, StreamObserver<Profile> out) {
        profiles.findByIccid(req.getIccid()).ifPresentOrElse(
            p -> { out.onNext(toProto(p)); out.onCompleted(); },
            () -> out.onError(Status.NOT_FOUND.withDescription("iccid " + req.getIccid()).asRuntimeException()));
    }
}
```
```bash
# call it without writing a client; server reflection must be enabled
$ grpcurl -plaintext -d '{"iccid":"8999600000000000001"}' esim.internal:9090 esim.v1.Provisioning/GetProfile
{ "iccid": "8999600000000000001", "state": "INSTALLED", "eid": "8903..." }
$ grpcurl -plaintext -d '{"iccid":"x"}' esim.internal:9090 esim.v1.Provisioning/GetProfile
ERROR: Code: NotFound  Message: iccid x
```

## Pitfalls
- Reusing a field number after deleting a field: old data decodes into the wrong field with no error. Always `reserved`.
- Treating `0`/`""` as "present": proto3 scalars are not nullable; use `optional`, wrappers or an explicit enum `UNSPECIFIED = 0`.
- Putting gRPC behind an L4 load balancer: one long-lived HTTP/2 connection sends every call to the same pod; the others idle. Use an L7 proxy (Envoy, nginx `grpc_pass`), a service mesh, or client-side `round_robin` over headless-service DNS ([[dns-resolution]]).
- No deadline on the client: the default is infinite, and a stuck upstream holds the thread or stream forever.
- Large messages: default max 4 MB; streaming or chunking is the fix, not raising the limit to 1 GB.
- Related: [[service-communication-sync-vs-async]] for when RPC is the wrong tool altogether, [[rest-and-http]] for the REST side of the comparison.
