(() => {
    const modal = document.getElementById('booking-modal');
    if (!modal) return;
    const form = modal.querySelector('[data-booking-form]');
    const title = modal.querySelector('#booking-modal-title');
    const submit = modal.querySelector('[data-booking-submit]');
    const errors = modal.querySelector('[data-form-errors]');
    const deleteForm = document.getElementById('booking-delete-form');
    const deleteButton = modal.querySelector('[data-booking-delete]');
    let opener;
    let saving = false;
    let originalTour;
    let editingBooking = false;
    let originalData = {};
    function departureChanged() {
        const select = form.elements.namedItem('tour');
        const option = select?.selectedOptions[0];
        if (!option?.value) return;
        for (const [field, key] of [['tour_date', 'date'], ['tour_time', 'time'], ['guide', 'guide']]) {
            modal.querySelector(`[data-context="${field}"]`).value = option.dataset[key] || '';
        }
        modal.querySelector('[data-reschedule-notice]').hidden = !editingBooking || select.value === originalTour;
        const moved = select.value !== originalTour;
        for (const [field, value] of Object.entries(moved ? {vendor:'Manual/Walk-up', booking_code:'Assigned on save', event_id:select.value} : originalData)) {
            if (['vendor', 'booking_code', 'event_id'].includes(field)) modal.querySelector(`[data-context="${field}"]`).value = value || '';
        }
    }
    form.elements.namedItem('tour')?.addEventListener('change', departureChanged);

    function fill(data, editing, preserveValues = false) {
        originalTour = String(data.tour || '');
        originalData = data;
        editingBooking = editing;
        title.textContent = editing ? 'Edit booking' : 'Add booking';
        submit.textContent = editing ? 'Save booking' : 'Add booking';
        if (!preserveValues) {
            form.reset();
            for (const [key, value] of Object.entries(data)) {
                const input = form.elements.namedItem(key);
                if (input) input.value = value ?? '';
            }
            errors.hidden = true;
            errors.replaceChildren();
        }
        modal.querySelectorAll('[data-context]').forEach(input => {
            input.value = data[input.dataset.context] ?? '';
        });
        const original = modal.querySelector('[data-original-counts]');
        original.hidden = !editing;
        original.textContent = `Originally booked: ${data.original_adults ?? 0} adults · ${data.original_children ?? 0} kids`;
        departureChanged();
    }

    window.openBookingModal = trigger => {
        if (saving) return;
        opener = trigger;
        const editing = trigger.dataset.mode === 'edit';
        fill(JSON.parse(trigger.dataset.booking), editing);
        form.action = trigger.dataset.action;
        form.elements.namedItem('return_url').value = location.pathname + location.search;
        deleteForm.action = trigger.dataset.deleteAction || '';
        deleteButton.hidden = !trigger.dataset.deleteAction;
        // Preloaded form; focus the heading instead of invoking contact/password autofill.
        modal.showModal();
        modal.scrollTop = 0;
    };
    modal.querySelectorAll('[data-close-booking-modal]').forEach(button => {
        button.addEventListener('click', () => modal.close());
    });
    modal.addEventListener('close', () => opener?.focus({preventScroll: true}));
    modal.addEventListener('click', event => {
        const bounds = modal.getBoundingClientRect();
        if (event.target === modal && (event.clientX < bounds.left || event.clientX > bounds.right ||
            event.clientY < bounds.top || event.clientY > bounds.bottom)) modal.close();
    });
    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (saving) return;
        saving = true;
        submit.disabled = true;
        form.setAttribute('aria-busy', 'true');
        try {
            const response = await fetch(form.action, {
                method: 'POST', body: new FormData(form), headers: {'Accept': 'application/json'},
            });
            const result = await response.json();
            if (!response.ok) {
                errors.replaceChildren();
                for (const [field, messages] of Object.entries(result.errors || {})) {
                    const label = form.elements.namedItem(field)?.closest('label')?.firstChild?.textContent?.trim();
                    for (const message of messages) {
                        const line = document.createElement('p');
                        line.textContent = label ? `${label}: ${message}` : message;
                        errors.append(line);
                    }
                }
                if (!errors.childNodes.length) errors.textContent = 'Could not save the booking. Please try again.';
                errors.hidden = false;
                errors.scrollIntoView({block: 'nearest'});
            } else {
                location.assign(result.redirect);
            }
        } catch (_) {
            errors.textContent = 'Could not confirm whether the booking saved. Check the page before trying again.';
            errors.hidden = false;
        } finally {
            saving = false;
            submit.disabled = false;
            form.removeAttribute('aria-busy');
        }
    });
    if (modal.dataset.openOnLoad === 'true') {
        fill(JSON.parse(modal.dataset.initial || '{}'), modal.dataset.mode === 'edit', true);
        modal.showModal();
    }
})();
