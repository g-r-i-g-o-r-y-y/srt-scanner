// Service worker for the GitHub Pages build: keeps every file the app needs in the browser's cache so
// it opens and works offline after the first visit. build.py fills in VERSION and FILES.
const VERSION = "__VERSION__";
const FILES = __FILES__;
const CACHE = "srt-scanner-" + VERSION;

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k.startsWith("srt-scanner-") && k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// Cache first: a new version arrives with a new sw.js (its VERSION changes), which re-caches everything.
self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET" || new URL(req.url).origin !== location.origin) return;
  e.respondWith(caches.open(CACHE).then(async c => {
    const hit = await c.match(req, {ignoreSearch: true}) || (req.mode === "navigate" && await c.match("./"));
    return hit || fetch(req);
  }));
});
