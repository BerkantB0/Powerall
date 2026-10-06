// Household identity is established by the backend session, never browser IDs.
import { readFileSync } from "node:fs";
export const dynamic = "force-dynamic";
const allowed = new Set(["/api/status", "/api/dashboard", "/api/tariffs", "/api/tariffs/delete", "/api/sync", "/api/auth/login", "/api/auth/register", "/api/auth/logout", "/api/auth/password", "/api/connection", "/api/account/delete", "/api/account/export", "/api/config"]);

async function boundedBody(request: Request) {
  const reader = request.body?.getReader();
  if (!reader) return "";
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > 16384) { await reader.cancel(); throw new Error("body-limit"); }
    chunks.push(value);
  }
  return Buffer.concat(chunks).toString("utf8");
}
async function proxy(request: Request) {
  const url = new URL(request.url);
  if (!allowed.has(url.pathname)) return Response.json({ error: "Endpoint not found." }, { status: 404 });
  const hosted = process.env.POWERALL_MODE === "hosted";
  const port = process.env.POWERALL_PORT || "8765";
  if (!/^\d{2,5}$/.test(port)) return Response.json({ error: "Invalid data service port." }, { status: 500 });
  if (hosted && request.method === "POST" && request.headers.get("origin") !== process.env.POWERALL_PUBLIC_ORIGIN) {
    return Response.json({ error: "Origin refused." }, { status: 403 });
  }
  const headers = new Headers();
  for (const name of ["content-type", "origin", "x-powerall-token", "cookie", "sec-fetch-site"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  try {
    if (hosted) {
      const path = process.env.POWERALL_INTERNAL_TOKEN_FILE;
      if (!path) throw new Error("missing-secret");
      headers.set("X-Powerall-Internal", readFileSync(path, "utf8").trim());
      // The private Caddy gateway overwrites this from its verified client IP.
      // No public frontend port is published.
      headers.set("X-Powerall-Client-IP", request.headers.get("x-powerall-client-ip") || "unknown");
    }
    const response = await fetch(`http://${hosted ? "api" : "127.0.0.1"}:${port}${url.pathname}${url.search}`, {
      method: request.method, headers,
      body: request.method === "POST" ? await boundedBody(request) : undefined,
      signal: AbortSignal.timeout(35000), cache: "no-store",
    });
    const outgoing = new Headers({ "Content-Type": "application/json", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" });
    const cookie = response.headers.get("set-cookie");
    if (cookie) outgoing.set("Set-Cookie", cookie);
    return new Response(await response.text(), { status: response.status, headers: outgoing });
  } catch (error) {
    if (error instanceof Error && error.message === "body-limit") return Response.json({ error: "Request body is too large." }, { status: 413 });
    return Response.json({ error: "The data service is unavailable. Please try again later." }, { status: 503 });
  }
}
export const GET = proxy;
export const POST = proxy;
