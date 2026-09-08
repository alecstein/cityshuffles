(async () => {
    document.body.addEventListener('htmx:configRequest', event => {
        event.detail.headers['X-Page-Visible'] = document.hidden ? 'no' : 'yes';
    });
    const panel = document.querySelector('[data-notification-settings]');
    const status = panel?.querySelector('[role=status]');
    const say = text => { if (status) status.textContent = text; };
    if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window) || !isSecureContext) {
        say('Notifications need a supported browser and HTTPS. On iPhone: iOS 16.4 or later, add CityShuffles to your Home Screen, then open it from there.');
        return;
    }
    try {
        const config = await (await fetch('/notifications/config/')).json();
        const registration = await navigator.serviceWorker.register('/push-worker.js');
        await navigator.serviceWorker.ready;
        const previews = panel?.querySelector('[name=push-previews]');
        const preferenceKey = `push-previews-${config.account}`;
        if (previews) previews.checked = localStorage.getItem(preferenceKey) === 'true';
        const post = async (subscription, action) => {
            const response = await fetch('/notifications/device/', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRFToken': config.csrf}, body: JSON.stringify({...subscription.toJSON(), action, previews: localStorage.getItem(preferenceKey) === 'true'})});
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || 'Could not save notification settings.');
        };
        const refresh = () => {
            for (const id of ['conversation-list', 'message-list', 'nav-unread']) {
                const element = document.getElementById(id);
                if (element && window.htmx) htmx.trigger(element, 'notification-refresh');
            }
        };
        navigator.serviceWorker.addEventListener('message', event => { if (event.data?.type === 'new-message') refresh(); });
        const syncRead = async () => {
            if (document.hidden) return;
            try {
                const response = await fetch('/notifications/unread/');
                if (!response.ok) return;
                const data = await response.json();
                // Only clear notifications if the server returned the complete unread set.
                if (data.count <= data.ids.length) registration.active?.postMessage({type:'shared-read', ids:data.ids});
                if ('setAppBadge' in navigator) await (data.count ? navigator.setAppBadge(data.count) : navigator.clearAppBadge());
            } catch (_) { /* Existing polling remains available offline. */ }
        };
        document.addEventListener('visibilitychange', () => { if (!document.hidden) { refresh(); syncRead(); } });
        setInterval(syncRead, 8000);
        syncRead();
        if (!config.publicKey) { say('Server setup is still needed before notifications can be enabled.'); return; }
        let subscription = await registration.pushManager.getSubscription();
        if (subscription && Notification.permission === 'granted') await post(subscription, 'subscribe');
        say(subscription ? 'Notifications enabled on this device.' : 'Enable notifications on each phone or computer you use.');
        panel?.querySelectorAll('button').forEach(button => button.disabled = false);
        panel?.addEventListener('click', async event => {
            const action = event.target.dataset.pushAction;
            if (!action) return;
            event.target.disabled = true;
            try {
                if (action === 'enable') {
                    const permission = await Notification.requestPermission();
                    if (permission !== 'granted') throw new Error('Notifications are blocked. Allow them in your browser/device settings.');
                    const raw = atob(config.publicKey.replace(/-/g, '+').replace(/_/g, '/'));
                    subscription = await registration.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: Uint8Array.from(raw, char => char.charCodeAt(0))});
                    await post(subscription, 'subscribe');
                    say('Notifications enabled on this device.');
                } else if (subscription && action === 'disable') {
                    await post(subscription, 'remove');
                    await subscription.unsubscribe();
                    subscription = null;
                    say('Notifications disabled on this device.');
                } else if (subscription && action === 'test') {
                    await post(subscription, 'test');
                    say('Test notification queued. If it does not arrive, check that the notification worker is running.');
                } else say('Enable notifications first.');
            } catch (error) { say(error.message); }
            finally { event.target.disabled = false; }
        });
        previews?.addEventListener('change', async () => {
            localStorage.setItem(preferenceKey, previews.checked);
            try { if (subscription) await post(subscription, 'subscribe'); } catch (error) { say(error.message); }
        });
    } catch (_) { say('Could not connect notification settings. Please reload and try again.'); }
})();
