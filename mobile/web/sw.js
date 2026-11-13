self.addEventListener("push", event => {
  const payload = event.data.json();
  event.waitUntil(self.registration.showNotification(payload.title, {
    body: payload.body, icon: "/icon-192.png",
  }));
});
self.addEventListener("notificationclick", event => {
  event.notification.close();
  event.waitUntil(clients.matchAll({ type: "window" }).then(windows => {
    return windows.length ? windows[0].focus() : clients.openWindow("/");
  }));
});
