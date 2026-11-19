import assert from "node:assert/strict";
import { test } from "node:test";

import { handlePublicRequest, limitedBody, type EdgeEnv } from "../src/edge.ts";

function environment(allowed = true): EdgeEnv {
  return {
    IMAGE_RATE_LIMIT: {
      limit: async () => ({ success: allowed }),
    },
    ORIGIN_AUTH_TOKEN: "private-test-secret",
    REQUEST_LIMIT_BYTES: "10",
    MULTIPART_OVERHEAD_BYTES: "4",
  } as unknown as EdgeEnv;
}

test("Worker forwards health requests with edge-owned auth and no-store", async () => {
  const incoming = new Request("https://api.example/healthz", {
    headers: {
      "X-Origin-Auth": "caller-value",
      "CF-Connecting-IP": "192.0.2.4",
    },
  });
  let upstreamHeaders: Headers | undefined;
  const response = await handlePublicRequest(incoming, environment(), async (upstream) => {
    upstreamHeaders = upstream.headers;
    return new Response("ok", { headers: { "cache-control": "public, max-age=60" } });
  });

  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "private, no-store");
  assert.match(response.headers.get("x-request-id") || "", /^[0-9a-f-]{36}$/);
  assert.equal(upstreamHeaders?.get("x-origin-auth"), "private-test-secret");
  assert.notEqual(upstreamHeaders?.get("x-origin-auth"), "caller-value");
});

test("Worker rate limits every processing attempt before forwarding", async () => {
  const incoming = new Request("https://api.example/v1/process", { method: "POST" });
  let forwardCalled = false;
  const response = await handlePublicRequest(incoming, environment(false), async () => {
    forwardCalled = true;
    return new Response("unexpected");
  });

  assert.equal(response.status, 429);
  assert.equal(response.headers.get("retry-after"), "60");
  assert.equal(response.headers.get("cache-control"), "private, no-store");
  assert.equal(forwardCalled, false);
});

test("Worker rejects an oversized declared body at the edge", async () => {
  const incoming = new Request("https://api.example/v1/process", {
    method: "POST",
    headers: { "content-length": "15" },
    body: "x",
  });
  let forwardCalled = false;
  const response = await handlePublicRequest(
    incoming,
    { ...environment(), REQUEST_LIMIT_BYTES: "10", MULTIPART_OVERHEAD_BYTES: "4" },
    async () => {
      forwardCalled = true;
      return new Response("unexpected");
    },
  );
  assert.equal(response.status, 413);
  assert.equal(forwardCalled, false);
});

test("streaming size guard cancels bodies without a Content-Length", async () => {
  let tripped = false;
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new Uint8Array([1, 2, 3]));
      controller.close();
    },
  });
  const limited = limitedBody(body, 2, () => {
    tripped = true;
  });
  await assert.rejects(() => new Response(limited).arrayBuffer());
  assert.equal(tripped, true);
});

test("Worker does not expose unknown paths or methods to the origin", async () => {
  let forwardCalled = false;
  const forward = async () => {
    forwardCalled = true;
    return new Response("unexpected");
  };
  const unknown = await handlePublicRequest(
    new Request("https://api.example/internal"),
    environment(),
    forward,
  );
  const wrongMethod = await handlePublicRequest(
    new Request("https://api.example/healthz", { method: "POST" }),
    environment(),
    forward,
  );
  assert.equal(unknown.status, 404);
  assert.equal(wrongMethod.status, 405);
  assert.equal(forwardCalled, false);
});
