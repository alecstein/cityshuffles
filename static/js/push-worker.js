self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('push', event => {
    event.waitUntil((async () => {
        const data = event.data ? event.data.json() : {};
        await self.registration.showNotification(data.title || 'CityShuffles', {
            body: data.body || 'New guest message', tag: data.tag, icon: '/static/push-icon.svg',
            data: {url: data.url || '/messages/'},
        });
        for (const client of await self.clients.matchAll({type: 'window'})) client.postMessage({type: 'new-message'});
    })());
});
self.addEventListener('notificationclick', event => {
    event.notification.close();
    event.waitUntil((async () => {
        let url = new URL(event.notification.data?.url || '/messages/', self.location.origin);
        if (url.origin !== self.location.origin) url = new URL('/messages/', self.location.origin);
        const windows = await self.clients.matchAll({type: 'window'});
        if (windows.length) {
            await windows[0].navigate(url.href);
            return windows[0].focus();
        }
        return self.clients.openWindow(url.href);
    })());
});
self.addEventListener('message', event => {
    if (event.data?.type !== 'shared-read') return;
    event.waitUntil((async () => {
        const notifications = await self.registration.getNotifications();
        const unread = new Set(event.data.ids.map(id => `message-${id}`));
        for (const notification of notifications) {
            if (notification.tag?.startsWith('message-') && !unread.has(notification.tag)) notification.close();
        }
    })());
});
