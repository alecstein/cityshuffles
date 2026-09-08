(() => {
    const panel = document.querySelector('[data-photo-upload]');
    if (!panel) return;
    const input = panel.querySelector('[data-photo-files]');
    const status = panel.querySelector('[data-photo-progress]');
    const grid = panel.querySelector('[data-photo-grid]');
    const preview = panel.closest('.photo-gallery')?.querySelector('[data-photo-preview]');
    const csrf = panel.querySelector('[name=csrfmiddlewaretoken]').value;
    let busy = false;
    function syncGallery() {
        const hasPhotos = !!grid.querySelector('figure');
        if (preview) preview.hidden = !hasPhotos;
        document.querySelectorAll('[data-send-photos]').forEach(button => {
            button.disabled = !hasPhotos;
            if (hasPhotos) button.removeAttribute('title');
            else button.title = 'Upload photos first';
        });
    }
    document.body.addEventListener('htmx:afterSwap', syncGallery);
    window.addEventListener('beforeunload', event => { if (busy) { event.preventDefault(); event.returnValue = ''; } });
    function addPhoto(photo) {
        const figure = document.createElement('figure');
        const link = document.createElement('a');
        link.href = photo.display; link.target = '_blank'; link.rel = 'noopener';
        const image = document.createElement('img');
        image.src = photo.thumbnail; image.alt = photo.filename; image.loading = 'lazy';
        link.append(image);
        const caption = document.createElement('figcaption');
        const name = document.createElement('span'), size = document.createElement('small');
        name.textContent = photo.filename; size.textContent = photo.width + ' × ' + photo.height;
        caption.append(name, size);
        const remove = document.createElement('button');
        remove.type = 'button'; remove.className = 'photo-remove';
        remove.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 7 10 10M17 7 7 17"></path></svg>';
        remove.setAttribute('aria-label', 'Delete ' + photo.filename); remove.dataset.photoRemove = photo.remove;
        figure.append(link, caption, remove); grid.append(figure);
        const previewLink = preview?.querySelector('a');
        if (previewLink) previewLink.href = photo.url;
        syncGallery();
    }
    function upload(job) {
        return new Promise(resolve => {
            const fail = text => { job.row.className = 'upload-failed'; job.row.textContent = job.file.name + ': ' + text; resolve(); };
            if (job.file.size > 50 * 1024 * 1024) return fail('Exceeds 50 MB limit.');
            const xhr = new XMLHttpRequest(), body = new FormData();
            body.append('photo', job.file); body.append('upload_id', crypto.randomUUID());
            xhr.open('POST', panel.dataset.photoUpload); xhr.setRequestHeader('X-CSRFToken', csrf); xhr.timeout = 600000;
            xhr.upload.onprogress = event => { if (event.lengthComputable) job.row.textContent = job.file.name + ': ' + Math.round(event.loaded / event.total * 100) + '%'; };
            xhr.onerror = () => fail('Connection interrupted.');
            xhr.ontimeout = () => fail('Upload timed out.');
            xhr.onload = () => {
                let result;
                try { result = JSON.parse(xhr.responseText); } catch (_) { return fail('Upload could not be confirmed.'); }
                if (xhr.status < 200 || xhr.status >= 300) return fail(result.error || 'Upload failed.');
                addPhoto(result); job.row.remove(); resolve();
            };
            xhr.send(body);
        });
    }
    input.addEventListener('change', async () => {
        if (busy || !input.files.length) return;
        status.replaceChildren();
        const pending = Array.from(input.files, file => {
            const row = document.createElement('p'); row.textContent = file.name + ': waiting'; status.append(row);
            return {file, row};
        });
        busy = true; input.disabled = true;
        async function worker() { while (pending.length) await upload(pending.shift()); }
        try { await Promise.all([worker(), worker()]); }
        finally { busy = false; input.disabled = false; input.value = ''; }
    });
    panel.addEventListener('click', async event => {
        const button = event.target.closest('[data-photo-remove]');
        if (!button || button.disabled) return;
        button.disabled = true;
        try {
            const response = await fetch(button.dataset.photoRemove, {method: 'POST', headers: {'X-CSRFToken': csrf}});
            if (!response.ok) throw new Error();
            button.closest('figure').remove(); syncGallery();
        } catch (_) {
            const error = document.createElement('p'); error.className = 'upload-failed';
            error.textContent = 'Could not remove the photo.'; status.append(error); button.disabled = false;
        }
    });
    syncGallery();
})();
