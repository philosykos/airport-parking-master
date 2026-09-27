// 모바일에서 <select>를 누르면 네이티브 목록 대신 바텀시트 목록을 연다.
(() => {
    'use strict';
    const UI = window.UI = window.UI || {};

    UI.SelectPicker = class {
        constructor(overlay) {
            this.overlay = overlay;
            this.title = overlay.querySelector('[data-select-title]');
            this.body = overlay.querySelector('[data-select-body]');
            this.select = null;
            this.sheet = new UI.Sheet(overlay, {
                panel: overlay.querySelector('.select-picker'),
                handle: overlay.querySelector('.select-picker-handle'),
                closeButtons: [...overlay.querySelectorAll('.select-picker-close')],
                backdrop: overlay,
                onClose: () => { this.select = null; },
            });
            this.body.addEventListener('click', event => {
                const item = event.target.closest('.select-picker-item');
                if (item) this.choose(item.dataset.value);
            });
        }
        attach(container) {
            container.querySelectorAll('select').forEach(select => {
                const intercept = event => {
                    if (!UI.isMobile() || select.matches(':disabled')) return;
                    event.preventDefault();
                    this.open(select);
                };
                select.addEventListener('mousedown', intercept);
                select.addEventListener('touchend', intercept);
            });
        }
        open(select) {
            this.select = select;
            this.title.textContent = select.closest('.field-group')?.querySelector('label')?.textContent || '선택';
            const items = [...select.options].filter((option, index) => !(index === 0 && !option.value)).map(option => {
                const item = document.createElement('button');
                item.type = 'button';
                item.className = 'select-picker-item' + (option.value === select.value ? ' selected' : '');
                item.dataset.value = option.value;
                item.textContent = option.textContent;
                return item;
            });
            this.body.replaceChildren(...items);
            this.sheet.open();
            requestAnimationFrame(() => this.body.querySelector('.selected')?.scrollIntoView({block: 'center'}));
        }
        choose(value) {
            const select = this.select;
            if (!select) return;
            select.value = value;
            select.dispatchEvent(new Event('change', {bubbles: true}));
            select.dispatchEvent(new Event('input', {bubbles: true}));
            this.sheet.close();
        }
    };
})();
