// 공통 로그 패널. 데스크톱에서는 오른쪽 패널이고, 모바일에서는 같은 요소가 바텀시트가 된다.
// entry: {time, type: {label, variant}, status: {label, tone}, summary, detail: [{title, kind, body, method}]}
(() => {
    'use strict';
    const UI = window.UI = window.UI || {};
    const $ = id => document.getElementById(id);
    const JSON_TOKEN = /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?|\bnull\b)/g;

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function formatJson(raw) {
        try { return JSON.stringify(JSON.parse(raw), null, 2); } catch (_) { return raw; }
    }

    function highlight(container, json) {
        let last = 0;
        for (const match of json.matchAll(JSON_TOKEN)) {
            const token = match[0];
            const cls = token.startsWith('"') ? (token.endsWith(':') ? 'json-key' : 'json-string')
                : /^(true|false)$/.test(token) ? 'json-bool' : token === 'null' ? 'json-null' : 'json-number';
            container.append(json.slice(last, match.index), el('span', cls, token));
            last = match.index + token.length;
        }
        container.append(json.slice(last));
    }

    function tag(type) {
        return el('span', `tag tag-${type?.variant || 'event'}`, type?.label || '');
    }

    function chip(status) {
        const node = el('span', 'cell-status', String(status?.label ?? '—'));
        node.dataset.tone = status?.tone || 'idle';
        return node;
    }

    UI.LogPanel = class {
        constructor(root) {
            this.root = root;
            this.body = $('log-body');
            this.count = $('log-count');
            this.badge = $('log-fab-badge');
            this.detail = $('detail-overlay');
            this.emptyText = this.body.dataset.emptyText || '아직 기록이 없습니다';
            this.entries = [];
            this.sheet = new UI.Sheet(root, {
                handle: root.querySelector('.log-sheet-handle'),
                closeButtons: [...root.querySelectorAll('.log-sheet-close')],
                backdrop: $('log-backdrop'),
            });
            $('log-fab')?.addEventListener('click', () => this.sheet.toggle());
            this.body.addEventListener('click', event => {
                const row = event.target.closest('tr.log-row');
                if (row) this.openDetail(this.entries[Number(row.dataset.index)]);
            });
            this.body.addEventListener('keydown', event => {
                const row = event.target.closest('tr.log-row');
                if (row && event.key === 'Enter') this.openDetail(this.entries[Number(row.dataset.index)]);
            });
            this.detail?.querySelector('.detail-close')?.addEventListener('click', () => UI.layers.close(this.detail));
            this.detail?.addEventListener('click', event => {
                if (event.target === this.detail) UI.layers.close(this.detail, {reason: 'backdrop'});
            });
            const scroll = root.querySelector('.log-scroll');
            const table = root.querySelector('.log-table-container');
            scroll?.addEventListener('scroll', () => table.classList.toggle('scrolled', scroll.scrollTop > 0));
            this.render([]);
        }

        openSheet() {
            // 완료 안내가 떠 있을 때 시트가 그 위로 올라와 안내를 가리지 않게 한다.
            if (UI.isMobile() && !UI.completion?.isOpen()) this.sheet.open();
        }

        render(entries) {
            const previous = this.entries.length;
            this.entries = entries || [];
            this.count.textContent = String(this.entries.length);
            if (this.badge) this.badge.textContent = this.entries.length ? String(this.entries.length) : '';
            if (this.entries.length !== previous) {
                this.count.classList.remove('bounce');
                void this.count.offsetWidth;
                this.count.classList.add('bounce');
            }
            if (!this.entries.length) {
                const cell = el('td', 'empty-msg');
                cell.colSpan = 4;
                const icon = el('div', 'empty-icon');
                icon.append(el('span', 'material-symbols-outlined', 'assignment'));
                cell.append(icon, this.emptyText);
                const row = el('tr');
                row.append(cell);
                this.body.replaceChildren(row);
                return;
            }
            const rows = this.entries.map((entry, index) => [entry, index]).reverse().map(([entry, index], order) => {
                const row = el('tr', entry.type?.variant === 'event' ? 'log-row row-event' : 'log-row');
                row.dataset.index = String(index);
                row.tabIndex = 0;
                row.style.animationDelay = Math.min(order * 50, 250) + 'ms';
                const summary = String(entry.summary ?? '');
                const typeCell = el('td');
                typeCell.append(tag(entry.type));
                const statusCell = el('td');
                statusCell.append(chip(entry.status));
                row.append(el('td', 'cell-time', entry.time || ''), typeCell, statusCell,
                    el('td', 'body-cell', summary.length > 80 ? summary.slice(0, 80) + '…' : summary));
                return row;
            });
            this.body.replaceChildren(...rows);
        }

        openDetail(entry) {
            if (!entry || !this.detail) return;
            $('detail-meta').replaceChildren(entry.time || '', el('span', 'detail-meta-sep', '|'), tag(entry.type),
                el('span', 'detail-meta-sep', '|'), chip(entry.status));
            const sections = (entry.detail || []).map(section => {
                const box = el('div', 'detail-section');
                box.append(el('div', 'detail-section-title', section.title));
                if (section.kind === 'url') {
                    const block = el('div', 'detail-url-block');
                    const method = section.method || 'POST';
                    const badge = el('span', 'detail-method-badge', method);
                    badge.dataset.method = method;
                    block.append(badge, el('span', 'detail-url-text', String(section.body ?? '')));
                    box.append(block);
                    return box;
                }
                const text = section.kind === 'json' ? formatJson(String(section.body ?? '')) : String(section.body ?? '');
                const code = el('div', 'detail-json');
                if (section.kind === 'json') highlight(code, text); else code.textContent = text;
                const copy = el('button', 'detail-copy-btn');
                copy.type = 'button';
                copy.append(el('span', 'material-symbols-outlined', 'content_copy'), ' 복사');
                copy.addEventListener('click', event => {
                    event.stopPropagation();
                    navigator.clipboard?.writeText(text).then(() => {
                        copy.classList.add('copied');
                        setTimeout(() => copy.classList.remove('copied'), 2000);
                    });
                });
                code.append(copy);
                box.append(code);
                return box;
            });
            $('detail-json').replaceChildren(...sections);
            UI.layers.open(this.detail);
        }
    };
})();
