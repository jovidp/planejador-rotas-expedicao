// Service worker mínimo: torna o site instalável como app no celular. Tudo vem sempre do servidor (sem cache).
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {});
