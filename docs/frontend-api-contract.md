# Frontend ↔ backend URL contract

Owner: DevOps. Audience: frontend author. Short version: **the browser only ever talks to its own origin.**

## Rules

1. **Use relative URLs.** REST: `fetch("/api/...")`. WebSocket: build from the page origin, e.g.
   `new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`)`.
2. **Never hardcode `localhost:8100`** (or any host/port). It works on your laptop and breaks everywhere else.
3. **`/api` prefix is stripped by nginx.** `GET /api/health` reaches core as `GET /health`. Don't put `/api` in core's routes.
4. **`/ws` is passed through unchanged** to core's `/ws` (no prefix stripped) with WebSocket upgrade and 1h timeouts.
5. **SSE-style paths** under `/api/` ending in `/stream` or `/events` are proxied unbuffered.
6. **`VITE_*` variables are build-time only.** They are inlined into the JS bundle by `vite build`; the runtime container
   has no Vite and ignores them. Changing one requires rebuilding the image. Don't put secrets in them.
   Nothing in the deployed app should need one for the backend URL.

## Local dev (`npm run dev`)

The Vite dev server on :5173 does not run nginx, so `/api` and `/ws` won't resolve unless you add a proxy to
`vite.config.ts` (your call — DevOps didn't touch it). Example:

```ts
server: { proxy: {
  "/api": { target: "http://localhost:8100", rewrite: (p) => p.replace(/^\/api/, "") },
  "/ws":  { target: "ws://localhost:8100", ws: true },
} }
```

Or skip local dev and use the container: `make up`, then open `http://localhost:${FRONTEND_PORT}` (default 8080).
