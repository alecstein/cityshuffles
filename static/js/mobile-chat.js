(() => {
    const shell = document.querySelector('.messenger-shell');
    const mobile = window.matchMedia('(max-width: 700px)');
    function setChatOpen(open) {
        shell.classList.toggle('mobile-chat-open', open);
        document.body.classList.toggle('mobile-chat-active', open);
    }
    function sizeViewport() {
        if (window.visualViewport) document.documentElement.style.setProperty('--visible-height', `${window.visualViewport.height}px`);
    }
    function resizeComposer() {
        const input = document.querySelector('.composer textarea');
        if (!input || !mobile.matches) return;
        input.style.height = 'auto';
        input.style.height = `${Math.min(112, Math.max(44, input.scrollHeight))}px`;
    }
    document.addEventListener('click', event => {
        if (!mobile.matches) return;
        const back = event.target.closest('.mobile-chat-back');
        if (back) {
            event.preventDefault();
            setChatOpen(false);
            history.pushState({}, '', back.href);
        }
    });
    document.addEventListener('htmx:afterSwap', event => {
        if (event.detail.target?.id === 'conversation-panel') {
            setChatOpen(true);
            document.body.classList.remove('mobile-composing');
            resizeComposer();
            const list = document.querySelector('#message-list');
            if (list) list.scrollTop = list.scrollHeight;
        }
    });
    window.addEventListener('popstate', () => setChatOpen(new URLSearchParams(location.search).has('conversation')));
    document.addEventListener('focusin', event => {
        if (event.target.matches('.composer textarea')) document.body.classList.add('mobile-composing');
    });
    document.addEventListener('focusout', event => {
        if (event.target.matches('.composer textarea')) document.body.classList.remove('mobile-composing');
    });
    document.addEventListener('input', event => { if (event.target.matches('.composer textarea')) resizeComposer(); });
    window.visualViewport?.addEventListener('resize', sizeViewport);
    window.addEventListener('resize', sizeViewport);
    mobile.addEventListener('change', () => {
        const input = document.querySelector('.composer textarea');
        if (input) input.style.height = '';
        document.body.classList.remove('mobile-composing');
        resizeComposer();
    });
    sizeViewport();
    setChatOpen(shell.classList.contains('mobile-chat-open'));
    resizeComposer();
})();
