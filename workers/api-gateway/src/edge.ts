export interface EdgeEnv {
  IMAGE_RATE_LIMIT: RateLimit;
  ORIGIN_AUTH_TOKEN?: string;
  REQUEST_LIMIT_BYTES?: string;
  MULTIPART_OVERHEAD_BYTES?: string;
}

export type OriginForwarder = (request: Request) => Promise<Response>;

const PROCESS_PATH = "/v1/process";
const HEALTH_PATHS = new Set(["/healthz", "/readyz"]);
const DEFAULT_UPLOAD_LIMIT = 10 * 1024 * 1024;
const DEFAULT_MULTIPART_OVERHEAD = 64 * 1024;

class RequestTooLarge extends Error {}

function json(status: number, code: string, message: string, requestId: string): Response {
  return new Response(JSON.stringify({ error: { code, message, request_id: requestId } }), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "private, no-store",
      "x-request-id": requestId,
    },
  });
}

function parseLimit(value: string | undefined, fallback: number): number {
  if (value === undefined) return fallback;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : fallback;
}

async function hashClientAddress(request: Request): Promise<string> {
  const address = request.headers.get("CF-Connecting-IP") || "unknown";
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(address));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function limitedBody(
  body: ReadableStream<Uint8Array>,
  maximum: number,
  onLimit: () => void,
): ReadableStream<Uint8Array> {
  let observed = 0;
  return body.pipeThrough(
    new TransformStream<Uint8Array, Uint8Array>({
      transform(chunk, controller) {
        observed += chunk.byteLength;
        if (observed > maximum) {
          onLimit();
          throw new RequestTooLarge();
        }
        controller.enqueue(chunk);
      },
    }),
  );
}

export async function handlePublicRequest(
  request: Request,
  env: EdgeEnv,
  forward: OriginForwarder,
): Promise<Response> {
  const requestId = crypto.randomUUID();
  const { pathname } = new URL(request.url);
  const isProcess = pathname === PROCESS_PATH;
  const isHealth = HEALTH_PATHS.has(pathname);

  if (!isProcess && !isHealth) return json(404, "not_found", "Unknown endpoint.", requestId);
  if ((isProcess && request.method !== "POST") || (isHealth && request.method !== "GET")) {
    return json(405, "method_not_allowed", "The request method is not supported.", requestId);
  }
  if (!env.ORIGIN_AUTH_TOKEN) {
    return json(503, "not_configured", "The service is temporarily unavailable.", requestId);
  }

  const headers = new Headers();
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  const accept = request.headers.get("accept");
  if (accept) headers.set("accept", accept);
  headers.set("x-request-id", requestId);
  headers.set("x-origin-auth", env.ORIGIN_AUTH_TOKEN);
  const clientIp = request.headers.get("CF-Connecting-IP");
  if (clientIp && clientIp.length <= 64) headers.set("x-client-ip", clientIp);

  let body: ReadableStream<Uint8Array> | null = null;
  let bodyExceededLimit = false;
  if (isProcess) {
    try {
      const { success } = await env.IMAGE_RATE_LIMIT.limit({ key: await hashClientAddress(request) });
      if (!success) {
        const response = json(429, "rate_limited", "Too many requests. Please retry later.", requestId);
        response.headers.set("retry-after", "60");
        return response;
      }
    } catch {
      return json(503, "rate_limiter_unavailable", "The service is temporarily unavailable.", requestId);
    }

    const uploadLimit = parseLimit(env.REQUEST_LIMIT_BYTES, DEFAULT_UPLOAD_LIMIT);
    const totalLimit = uploadLimit + parseLimit(env.MULTIPART_OVERHEAD_BYTES, DEFAULT_MULTIPART_OVERHEAD);
    const rawLength = request.headers.get("content-length");
    if (rawLength !== null) {
      if (!/^\d+$/.test(rawLength)) {
        return json(400, "invalid_request", "The request could not be parsed.", requestId);
      }
      if (Number(rawLength) > totalLimit) {
        return json(413, "payload_too_large", "The request exceeds the configured size limit.", requestId);
      }
    }
    if (!request.body) return json(400, "invalid_upload", "An image upload is required.", requestId);
    headers.delete("content-length");
    body = limitedBody(request.body, totalLimit, () => {
      bodyExceededLimit = true;
    });
  }

  try {
    const upstream = await forward(
      new Request(request.url, {
        method: request.method,
        headers,
        body,
        redirect: "manual",
      }),
    );
    const responseHeaders = new Headers(upstream.headers);
    responseHeaders.set("cache-control", "private, no-store");
    responseHeaders.set("x-request-id", requestId);
    responseHeaders.delete("set-cookie");
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    });
  } catch (error) {
    if (bodyExceededLimit || error instanceof RequestTooLarge) {
      return json(413, "payload_too_large", "The request exceeds the configured size limit.", requestId);
    }
    return json(503, "origin_unavailable", "The service is temporarily unavailable.", requestId);
  }
}
