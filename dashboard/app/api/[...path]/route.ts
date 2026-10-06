// Only the local data service handles Bright credentials and API tokens.
// Requests never carry provider secrets into React or the browser.
export const dynamic = "force-dynamic";

async function proxy(request: Request) {
  const url = new URL(request.url);
  const allowed = ["/api/status", "/api/dashboard", "/api/tariffs", "/api/tariffs/delete", "/api/sync"];
  if (!allowed.includes(url.pathname)) {
    return Response.json({ error: "Endpoint not found." }, { status: 404 });
  }
  const port = process.env.POWERALL_PORT || "8765";
  if (!/^\d{2,5}$/.test(port)) return Response.json({ error: "Invalid data service port." }, { status: 500 });
  const headers = new Headers();
  for (const name of ["content-type", "origin", "x-powerall-token"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  try {
    const response = await fetch(`http://127.0.0.1:${port}${url.pathname}${url.search}`, {
      method: request.method, headers,
      body: request.method === "POST" ? await request.text() : undefined,
      signal: AbortSignal.timeout(35000), cache: "no-store",
    });
    return new Response(await response.text(), {
      status: response.status,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" },
    });
  } catch {
    return Response.json({ error: "The local data service is unavailable. Start the app with python3 scripts/run.py." }, { status: 503 });
  }
}
export const GET = proxy;
export const POST = proxy;
