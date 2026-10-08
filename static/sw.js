/* Twelve – Service Worker */
const CACHE = 'twelve-v1';

// Install: cache the shell assets
self.addEventListener('install', (e) => {
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(clients.claim());
});

// Push: show notification
self.addEventListener('push', (e) => {
  let data = {};
  try { data = e.data ? e.data.json() : {}; } catch (_) {}

  const title   = data.title || 'Twelve';
  const options = {
    body:    data.body  || '',
    icon:    '/static/icons/icon-192.png',
    badge:   '/static/icons/icon-192.png',
    vibrate: [200, 100, 200],
    data:    { url: data.url || '/' },
    actions: [{ action: 'open', title: 'Ouvrir' }],
  };

  e.waitUntil(
    self.registration.showNotification(title, options).then(() => {
      try {
        return (self.navigator || navigator).setAppBadge(1);
      } catch (_) {}
    })
  );
});

// Click: open the challenge page
self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  // Badge cleared only when photo submitted, not on click
  const url = (e.notification.data && e.notification.data.url) || '/';
  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((cs) => {
      for (const c of cs) {
        if (c.url.includes(self.location.origin) && 'focus' in c) {
          c.navigate(url);
          return c.focus();
        }
      }
      return clients.openWindow(url);
    })
  );
});
