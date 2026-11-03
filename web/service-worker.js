const CACHE_VERSION = "lodicho-shell-v5";
const APP_SHELL = [
  "/",
  "/index.html",
  "/manifest.json",
  "/offline.html",
  "/css/styles.css",
  "/js/app.js",
  "/js/api.js",
  "/js/admin-api.js",
  "/js/state.js",
  "/js/util.js",
  "/js/icons.js",
  "/js/vendor/marked.min.js",
  "/js/components/top-header.js",
  "/js/components/navigation.js",
  "/js/components/composer.js",
  "/js/components/veredicto-badge.js",
  "/js/components/evidencia-sheet.js",
  "/js/components/banner-silencio.js",
  "/js/components/informe-card.js",
  "/js/views/home-view.js",
  "/js/views/resultado-view.js",
  "/js/views/historial-view.js",
  "/js/views/acerca-view.js",
  "/icons/icon.svg",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/icons/favicon-32.png",
  "/icons/favicon-16.png",
  "/icons/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_VERSION)
      .then((cache) => cache.addAll(APP_SHELL))
      .catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((key) => key !== CACHE_VERSION).map((key) => caches.delete(key)))
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(fetch(request).catch(() => caches.match("/offline.html")));
    return;
  }
  event.respondWith(
    caches.match(request).then((cached) => {
      const network = fetch(request)
        .then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(CACHE_VERSION).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() => cached || caches.match("/offline.html"));
      return cached || network;
    })
  );
});