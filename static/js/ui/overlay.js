// 겹쳐 뜨는 레이어(상세, 시트, 완료 오버레이)의 스택. 화면 쌓임(z-index)도 스택 순서를 따른다.
// Esc는 맨 위 레이어만 닫고, Tab 포커스는 맨 위 레이어 안에 가두며, 레이어가 하나라도 열려 있으면 본문 스크롤을 잠근다.
// 네이티브 <dialog>(설정 팝업)가 열려 있는 동안에는 dialog가 Esc와 포커스를 맡는다.
(() => {
    'use strict';
    const UI = window.UI = window.UI || {};
    const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
    const BASE_Z = 1000;
    const stack = [];
    let locks = 0;

    const focusables = element => [...element.querySelectorAll(FOCUSABLE)].filter(node => node.getClientRects().length > 0);
    const dialogOpen = () => document.querySelector('dialog[open]') !== null;
    const topLayer = () => stack.at(-1);
    // 물려받은 트리거가 화면 크기 변화 등으로 더는 보이지 않으면(연결 안 됨/렌더 안 됨/visibility:hidden)
    // 그쪽으로 포커스를 되돌리지 않는다.
    const canFocus = element => !!element && document.contains(element) && element.getClientRects().length > 0
        && getComputedStyle(element).visibility !== 'hidden';

    function trap(event) {
        const layer = topLayer();
        if (!layer || event.key !== 'Tab' || dialogOpen()) return;
        const items = focusables(layer.element);
        if (!items.length) { event.preventDefault(); return; }
        const first = items[0], last = items.at(-1);
        if (!layer.element.contains(document.activeElement)) {
            event.preventDefault();
            (event.shiftKey ? last : first).focus();
        } else if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    }

    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && stack.length && !dialogOpen()) {
            event.preventDefault();
            event.stopImmediatePropagation();
            UI.layers.close(topLayer().element, {reason: 'escape'});
            return;
        }
        trap(event);
    }, true);

    UI.layers = {
        lock() { if (locks++ === 0) document.body.style.overflow = 'hidden'; },
        unlock() { if (locks > 0 && --locks === 0) document.body.style.overflow = ''; },
        isOpen: element => stack.some(layer => layer.element === element),
        top: () => topLayer()?.element || null,
        open(element, {onClose, focus} = {}) {
            if (!element || this.isOpen(element)) return;
            const layer = {element, onClose, trigger: document.activeElement};
            stack.push(layer);
            element.style.zIndex = String(BASE_Z + stack.length * 10);
            element.classList.add('open');
            this.lock();
            requestAnimationFrame(() => {
                // 그사이 닫혔거나 다른 레이어가 위에 열렸으면 포커스를 옮기지 않는다.
                if (topLayer() !== layer || dialogOpen()) return;
                (focus || focusables(element)[0])?.focus({preventScroll: true});
            });
        },
        close(element, {reason = 'close'} = {}) {
            const index = stack.findIndex(layer => layer.element === element);
            if (index < 0) return;
            const wasTop = index === stack.length - 1;
            const [layer] = stack.splice(index, 1);
            element.classList.remove('open');
            element.style.zIndex = '';
            this.unlock();
            if (wasTop) {
                if (canFocus(layer.trigger)) layer.trigger.focus({preventScroll: true});
            } else {
                // 아래 레이어가 먼저 닫히면, 그 위 레이어가 닫힐 때 돌아갈 곳을 물려준다.
                stack[index].trigger = layer.trigger;
            }
            layer.onClose?.(reason);
        },
    };

    const overlay = () => document.getElementById('completion-overlay');

    UI.completion = {
        show({variant = 'success', icon, title = '', highlight = '', subtitle = '', actions, onDismiss} = {}) {
            const root = overlay();
            if (!root) return;
            UI.layers.close(root, {reason: 'replace'});
            root.dataset.variant = variant;
            root.querySelector('[data-completion-icon]').textContent = icon || (variant === 'success' ? 'check_circle' : 'notifications_active');
            const heading = root.querySelector('[data-completion-title]');
            heading.replaceChildren(document.createTextNode(title));
            if (highlight) {
                const mark = document.createElement('span');
                mark.className = 'success-highlight';
                mark.textContent = highlight;
                heading.append(document.createElement('br'), mark);
            }
            root.querySelector('[data-completion-subtitle]').textContent = subtitle;
            const list = actions?.length ? actions : [{label: '확인'}];
            const buttons = list.map(action => {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = action.variant === 'secondary' ? 'completion-secondary' : 'success-close-btn';
                button.textContent = action.label;
                button.addEventListener('click', () => {
                    UI.layers.close(root, {reason: 'action'});
                    action.onClick?.();
                });
                return button;
            });
            root.querySelector('[data-completion-actions]').replaceChildren(...buttons);
            UI.layers.open(root, {focus: buttons[0], onClose: reason => {
                if (reason !== 'action' && reason !== 'replace') onDismiss?.(reason);
            }});
        },
        success(options) { this.show({...options, variant: 'success'}); },
        action(options) { this.show({...options, variant: 'action'}); },
        close(reason = 'close') { UI.layers.close(overlay(), {reason}); },
        isOpen() { return UI.layers.isOpen(overlay()); },
    };
})();
