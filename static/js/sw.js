/* Service worker KAVKAZ-CAR. Отдаётся с адреса /sw.js (там подставляется версия).
 *
 * Что делает:
 *  • стили, скрипты и иконки кладёт в кэш — приложение открывается мгновенно;
 *  • если интернета нет — показывает страницу «Нет соединения» вместо ошибки браузера.
 * Чего НЕ делает: страницы сайта (кабинет, объявления, оплата, админка) НЕ кэшируются, поэтому
 * чужие данные не могут «застрять» на устройстве, а устаревших цен и статусов не бывает. */
const VERSION = "__VERSION__";
const CACHE = "kc-static-" + VERSION;
const OFFLINE_URL = "/offline";
const PRECACHE = [
  OFFLINE_URL,
  "/static/css/style.css?v=" + VERSION,
  "/static/js/app.js?v=" + VERSION,
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/img/logo.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("kc-static-") && k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

const CACHEABLE = /^\/static\/(css|js|icons|img)\//;

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.mode === "navigate") {
    // Страницы всегда с сервера; кэш — только запасной вариант при отсутствии сети.
    event.respondWith(fetch(request).catch(() => caches.match(OFFLINE_URL)));
    return;
  }
  if (CACHEABLE.test(url.pathname)) {
    event.respondWith(
      caches.match(request).then((hit) => hit || fetch(request).then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(request, copy));
        }
        return response;
      }))
    );
  }
});
