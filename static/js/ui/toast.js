(() => {
    'use strict';
    const UI = window.UI = window.UI || {};
    const ICONS = {success: 'check_circle', error: 'error', info: 'info'};
    const DURATION = 3000; // overlay.css .toast-progress 애니메이션 길이와 같다

    UI.toast = (message, type = 'info') => {
        const container = document.getElementById('toast-container');
        if (!container || !message) return null;
        while (container.children.length >= 3) container.firstChild.remove();
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.setAttribute('role', type === 'error' ? 'alert' : 'status');
        const icon = document.createElement('span');
        icon.className = 'material-symbols-outlined toast-icon';
        icon.setAttribute('aria-hidden', 'true');
        icon.textContent = ICONS[type] || ICONS.info;
        const text = document.createElement('span');
        text.textContent = message;
        const progress = document.createElement('div');
        progress.className = 'toast-progress';
        toast.append(icon, text, progress);
        container.append(toast);
        requestAnimationFrame(() => requestAnimationFrame(() => toast.classList.add('show')));
        let remaining = DURATION, started = Date.now(), timer;
        const dismiss = () => {
            toast.classList.remove('show');
            toast.classList.add('hiding');
            setTimeout(() => toast.remove(), 400);
        };
        const run = () => { started = Date.now(); timer = setTimeout(dismiss, remaining); };
        toast.addEventListener('mouseenter', () => {
            clearTimeout(timer);
            remaining -= Date.now() - started;
            progress.style.animationPlayState = 'paused';
        });
        toast.addEventListener('mouseleave', () => { progress.style.animationPlayState = 'running'; run(); });
        run();
        return toast;
    };
})();
