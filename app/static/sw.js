/* Avisa service worker — enables install ("add to home screen") and light
   offline support. Strategy:
   - navigations (pages): network-first, fall back to cache, then to the
     cached front page, so the app opens offline with the last paper.
   - /static/ assets: cache-first with background refresh.
   - everything else (e.g. the /status polling endpoint): straight to the
     network, never cached — so live progress is never served stale. */
const CACHE = "avisa-v2";
const SHELL = [
  "/",
  "/static/style.css",
  "/static/icon-192.png",
  "/static/icon-512.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return; // let cross-origin (fonts) pass

  if (req.mode === "navigate") {
    e.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
          return res;
        })
        .catch(() => caches.match(req).then((r) => r || caches.match("/")))
    );
    return;
  }

  if (url.pathname.startsWith("/static/")) {
    e.respondWith(
      caches.match(req).then((cached) => {
        const network = fetch(req)
          .then((res) => {
            if (res && res.status === 200) {
              const copy = res.clone();
              caches.open(CACHE).then((c) => c.put(req, copy));
            }
            return res;
          })
          .catch(() => cached);
        return cached || network;
      })
    );
  }
  // other GETs (e.g. /status) — default to the network, no caching.
});
