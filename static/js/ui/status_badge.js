// 헤더 상태 배지와 로그 헤더 상태를 같은 톤으로 함께 바꾼다.
(() => {
    'use strict';
    const UI = window.UI = window.UI || {};
    UI.statusBadge = {
        set({label, tone = 'idle', short} = {}) {
            const header = document.getElementById('header-status');
            if (header) {
                header.dataset.tone = tone;
                const headerText = document.getElementById('header-status-text');
                if (headerText) headerText.textContent = label;
            }
            const log = document.getElementById('status-badge');
            if (log) {
                log.dataset.tone = tone;
                const badgeLabel = log.querySelector('.status-label');
                if (badgeLabel) badgeLabel.textContent = short || label;
            }
        },
    };
})();
