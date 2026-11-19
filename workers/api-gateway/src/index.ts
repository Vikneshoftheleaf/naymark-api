import { DurableObject } from "cloudflare:workers";
import { handlePublicRequest, type EdgeEnv } from "./edge";

const PORT = 8080;
const SHARD_COUNT = 8;
const INACTIVITY_TIMEOUT_MS = 5 * 60 * 1000;
const STARTUP_TIMEOUT_MS = 30_000;

interface Env extends EdgeEnv {
  API_ORIGIN: DurableObjectNamespace<ApiContainer>;
}

export class ApiContainer extends DurableObject<Env> {
  private starting: Promise<void> | undefined;

  async fetch(request: Request): Promise<Response> {
    this.starting ??= this.startAndWaitForPort().finally(() => {
      this.starting = undefined;
    });
    await this.starting;

    const url = new URL(request.url);
    url.protocol = "http:";
    url.host = "container";
    const forwarded = new Request(url, request);
    forwarded.headers.delete("host");
    return this.ctx.container!.getTcpPort(PORT).fetch(forwarded);
  }

  private async startAndWaitForPort(): Promise<void> {
    const container = this.ctx.container;
    if (!container) throw new Error("Container binding is unavailable");
    if (!container.running) {
      container.start({
        image: container.images.base,
        instance: "standard-1",
        enableInternet: false,
        env: {
          ORIGIN_AUTH_TOKEN: this.env.ORIGIN_AUTH_TOKEN || "",
          MAX_CONCURRENT_REQUESTS: "1",
        },
      });
    }
    await container.setInactivityTimeout(INACTIVITY_TIMEOUT_MS);

    const port = container.getTcpPort(PORT);
    const deadline = Date.now() + STARTUP_TIMEOUT_MS;
    while (Date.now() < deadline) {
      try {
        const response = await port.fetch("http://container/healthz", {
          headers: { "X-Origin-Auth": this.env.ORIGIN_AUTH_TOKEN || "" },
          signal: AbortSignal.timeout(1_000),
        });
        await response.body?.cancel();
        if (response.ok) return;
      } catch {
        // The container process may be running before Uvicorn is ready.
      }
      await scheduler.wait(250);
    }
    throw new Error("Container readiness check timed out");
  }
}

export default {
  fetch(request: Request, env: Env): Promise<Response> {
    return handlePublicRequest(request, env, async (originRequest) => {
      const shard = crypto.getRandomValues(new Uint32Array(1))[0] % SHARD_COUNT;
      return env.API_ORIGIN.getByName(`api-shard-${shard}`).fetch(originRequest);
    });
  },
} satisfies ExportedHandler<Env>;
