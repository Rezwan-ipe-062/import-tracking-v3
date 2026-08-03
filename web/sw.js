const VERSION = 'anchor-v1';
const CORE = [
  './',
  './index.html',
  './manifest.json',
  './css/style.css',
  './js/app.js',
  './img/anchor-logo.png',
  './img/anchor-192.png',
  './img/anchor-512.png',
  './img/anchor-maskable-192.png',
  './img/anchor-maskable-512.png',
  './img/anchor-180.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(VERSION).then((cache) => cache.addAll(CORE)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.map((k) => {
      if (k !== VERSION) return caches.delete(k);
    }))).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== location.origin) return;
  event.respondWith(
    caches.match(request).then((cached) => {
      if (cached) return cached;
      return fetch(request).then((resp) => {
        const copy = resp.clone();
        caches.open(VERSION).then((cache) => cache.put(request, copy));
        return resp;
      });
    })
  );
});
