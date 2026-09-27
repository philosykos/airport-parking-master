// 헤더 상태 배지. 화면의 진행 상태는 이 배지 한 곳에만 표시한다.
(() => {
    'use strict';
    const UI = window.UI = window.UI || {};
    UI.statusBadge = {
        set({label, tone = 'idle'} = {}) {
            const header = document.getElementById('header-status');
            if (!header) return;
            header.dataset.tone = tone;
            const text = document.getElementById('header-status-text');
            if (text) text.textContent = label;
        },
    };
})();
