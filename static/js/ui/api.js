// 공통 fetch 래퍼. 모든 화면 JS가 이것으로 서버를 부른다.
(() => {
    'use strict';
    const UI = window.UI = window.UI || {};
    UI.isMobile = () => window.matchMedia('(max-width: 960px)').matches;
    UI.api = async (path, {method = 'GET', data} = {}) => {
        const options = {method, cache: 'no-store'};
        if (data !== undefined) {
            options.headers = {'Content-Type': 'application/json'};
            options.body = JSON.stringify(data);
        }
        const response = await fetch(path, options);
        let body = null;
        try { body = await response.json(); } catch (_) { body = null; }
        if (!response.ok || body === null) {
            const error = new Error(body?.error || `서버 오류 (HTTP ${response.status})`);
            error.status = response.status;
            error.data = body;
            throw error;
        }
        return body;
    };
})();
