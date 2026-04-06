/**
 * sw.js — Service Worker
 * Responsável por emitir notificações push mesmo com a aba em segundo plano.
 */

const CACHE_NAME = "bus-tracker-v1";
const ASSETS = ["/", "/sw.js"];

// ── Instalação ──────────────────────────────────────────────────────────────
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS))
  );
  self.skipWaiting();
});

// ── Ativação ────────────────────────────────────────────────────────────────
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

// ── Mensagens vindas da página principal ───────────────────────────────────
self.addEventListener("message", (event) => {
  const { type, payload } = event.data || {};

  if (type === "ALARM") {
    // Dispara notificação nativa do sistema operacional
    self.registration.showNotification("🚌 Prepare-se para desembarcar!", {
      body: payload?.message || "Seu ponto está a menos de 200 metros!",
      icon: "/static/icon-192.png",
      badge: "/static/badge-72.png",
      vibrate: [200, 100, 200, 100, 400],
      tag: "bus-alarm",           // substitui notificação anterior de mesmo tipo
      renotify: true,
      requireInteraction: true,   // não some automaticamente
      data: { url: self.location.origin },
    });
  }

  if (type === "ADMIN_NOTIFY") {
    self.registration.showNotification("📢 Aviso do Administrador", {
      body: payload?.message || "",
      icon: "/static/icon-192.png",
      tag: "admin-notify",
      renotify: true,
      requireInteraction: true,
    });
  }
});

// ── Clique na notificação ───────────────────────────────────────────────────
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      if (list.length > 0) return list[0].focus();
      return clients.openWindow(event.notification.data?.url || "/");
    })
  );
});

// ── Fetch (cache-first para assets) ────────────────────────────────────────
self.addEventListener("fetch", (event) => {
  // Não intercepta chamadas de API
  if (event.request.url.includes("/api/")) return;

  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
