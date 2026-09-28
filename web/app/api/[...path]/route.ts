/**
 * Runtime proxy to the conversion engine.
 *
 * The browser only ever talks to this origin, so the engine stays unpublished on
 * the Docker network and a VM deployment needs exactly one open port. Bodies and
 * responses are streamed rather than buffered, which is what lets large uploads,
 * large downloads and the job event stream all pass through unchanged.
 */

const ENGINE = process.env.ENGINE_URL ?? "http://localhost:8000";

// Hop-by-hop headers must not be forwarded, and the host header must not be
// copied or the engine sees the wrong origin.
const STRIP = new Set([
  "host",
  "connection",
  "keep-alive",
  "transfer-encoding",
  "upgrade",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "content-length",
  // undici rejects this outright ("expect header not supported"), and curl
  // adds it automatically for bodies over ~1 MB — so forwarding it broke every
  // large scripted upload while browsers, which never send it, worked fine.
  "expect",
]);

function forwardHeaders(source: Headers): Headers {
  const headers = new Headers();
  source.forEach((value, key) => {
    if (!STRIP.has(key.toLowerCase())) headers.set(key, value);
  });
  return headers;
}

async function proxy(request: Request): Promise<Response> {
  const incoming = new URL(request.url);
  const target = `${ENGINE}${incoming.pathname}${incoming.search}`;

  const init: RequestInit & { duplex?: "half" } = {
    method: request.method,
    headers: forwardHeaders(request.headers),
    redirect: "manual",
  };

  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = request.body;
    // Required by undici whenever a request body is a stream.
    init.duplex = "half";
  }

  let response: Response;
  try {
    response = await fetch(target, init);
  } catch (thrown) {
    // A swallowed proxy error is unactionable in production; log the cause.
    console.error(
      `proxy ${request.method} ${incoming.pathname} -> ${ENGINE}:`,
      thrown instanceof Error ? `${thrown.name}: ${thrown.message}` : thrown,
      thrown instanceof Error && thrown.cause ? `cause: ${String(thrown.cause)}` : "",
    );
    return Response.json(
      {
        error: {
          code: "engine_unreachable",
          title: "The conversion engine is not responding",
          detail:
            "The interface is running but could not reach the engine service on " +
            `${ENGINE}. It may still be starting up.`,
          suggestions: [
            "Wait a few seconds and try again.",
            "Check it with: docker compose logs engine",
          ],
        },
      },
      { status: 503 },
    );
  }

  const headers = forwardHeaders(response.headers);
  // Event streams must not be buffered anywhere between engine and browser.
  if (headers.get("content-type")?.includes("text/event-stream")) {
    headers.set("cache-control", "no-cache, no-transform");
    headers.set("x-accel-buffering", "no");
  }

  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

export const dynamic = "force-dynamic";
// Node runtime: the edge runtime cannot stream a request body to an upstream.
export const runtime = "nodejs";

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const PUT = proxy;
export const DELETE = proxy;
export const HEAD = proxy;
