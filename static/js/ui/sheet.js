// 모바일 바텀시트. 열림 표시는 root의 open 클래스이고, 손잡이를 100px 넘게 끌어내리면 닫는다.
(() => {
    'use strict';
    const UI = window.UI = window.UI || {};

    UI.Sheet = class {
        constructor(root, {panel, handle, closeButtons = [], backdrop, onClose} = {}) {
            this.root = root;
            this.panel = panel || root;
            this.backdrop = backdrop;
            this.onClose = onClose;
            closeButtons.forEach(button => button.addEventListener('click', () => this.close()));
            backdrop?.addEventListener('click', event => { if (event.target === backdrop) this.close('backdrop'); });
            if (handle) this.enableSwipe(handle);
            // 모바일 폭에서 연 채 데스크톱 폭으로 넓히면 시트가 레이어 스택에 남아 body 스크롤 잠금과
            // Esc·Tab 포커스 가두기가 보이지 않는 레이어에 걸리므로, 데스크톱으로 바뀌면 닫는다.
            window.matchMedia('(max-width: 960px)').addEventListener('change', event => {
                if (!event.matches && this.isOpen) this.close();
            });
        }
        get isOpen() { return UI.layers.isOpen(this.root); }
        open() {
            const separate = this.backdrop && this.backdrop !== this.root;
            UI.layers.open(this.root, {onClose: reason => {
                if (separate) {
                    this.backdrop.classList.remove('open');
                    this.backdrop.style.zIndex = '';
                }
                this.onClose?.(reason);
            }});
            if (separate) {
                this.backdrop.style.zIndex = String(Number(this.root.style.zIndex) - 1);
                this.backdrop.classList.add('open');
            }
        }
        close(reason = 'close') { UI.layers.close(this.root, {reason}); }
        toggle() { if (this.isOpen) this.close(); else this.open(); }
        enableSwipe(handle) {
            let startY = 0, offset = 0, dragging = false;
            handle.addEventListener('touchstart', event => {
                startY = event.touches[0].clientY;
                dragging = true;
                this.panel.style.transition = 'none';
            }, {passive: true});
            document.addEventListener('touchmove', event => {
                if (!dragging) return;
                offset = event.touches[0].clientY - startY;
                if (offset > 0) this.panel.style.transform = `translateY(${offset}px)`;
            }, {passive: true});
            document.addEventListener('touchend', () => {
                if (!dragging) return;
                dragging = false;
                this.panel.style.transition = '';
                this.panel.style.transform = '';
                if (offset > 100) this.close();
                offset = 0;
            });
        }
    };
})();
