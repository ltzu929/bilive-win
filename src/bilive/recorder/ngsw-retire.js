// Retire the legacy native UI cache that otherwise intercepts /workbench.
self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', event => event.waitUntil((async () => {
  await self.clients.claim();
  const names = await caches.keys();
  await Promise.all(names.filter(name => name.startsWith('ngsw:/:')).map(name => caches.delete(name)));
  await self.registration.unregister();
})()));
