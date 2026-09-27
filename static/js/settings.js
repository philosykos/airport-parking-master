(() => {
    'use strict';

    class NotificationSettings {
        constructor(element) {
            this.element = element;
            this.button = element.querySelector('[data-test]');
            this.busy = false;
            this.refreshId = 0;
            this.button.addEventListener('click', () => this.sendTest());
            this.refresh();
        }
        text(selector, value) {
            const node = this.element.querySelector(selector);
            node.textContent = value;
            if (['[data-error]', '[data-delivery]', '[data-test-result]', '[data-test-help]'].includes(selector)) node.hidden = !value;
        }
        async request(url, method = 'GET') {
            const response = await fetch(url, {method, cache: 'no-store',
                ...(method === 'POST' ? {headers: {'Content-Type': 'application/json'}, body: '{}'} : {})});
            if (!response.ok) throw new Error('request failed');
            return response.json();
        }
        render(status) {
            this.text('[data-connection]', status.credentialsConfigured ? '설정 완료' : '미설정');
            const label = result => window.NotificationStatus.label(result.status);
            this.text('[data-delivery]', status.lastDelivery ? `최근 알림: ${label(status.lastDelivery)}` : status.lastTest ? '' : '전송 내역 없음');
            this.text('[data-test-result]', status.lastTest ? `테스트: ${label(status.lastTest)}` : '');
            this.text('[data-error]', status.lastTest?.error || status.lastDelivery?.error || '');
            this.text('[data-test-help]', !status.enabled ? '알림을 켜면 테스트할 수 있습니다.' :
                !status.credentialsConfigured ? '봇 토큰과 수신자 ID를 설정해주세요.' : '');
            this.button.disabled = this.busy || !status.configured || status.testPending;
            this.button.textContent = this.busy || status.testPending ? '전송 중…' : '테스트 알림';
        }
        async refresh() {
            clearTimeout(this.timer);
            const id = ++this.refreshId;
            try {
                const status = await this.request(this.element.dataset.statusUrl);
                if (id !== this.refreshId) return;
                this.render(status);
            } catch (_) {
                if (id !== this.refreshId) return;
                this.button.disabled = true;
                this.button.textContent = '테스트 알림';
                this.text('[data-connection]', '확인 불가');
                this.text('[data-delivery]', '');
                this.text('[data-test-result]', '');
                this.text('[data-test-help]', '');
                this.text('[data-error]', '설정을 불러오지 못했습니다. 잠시 후 다시 확인합니다.');
            } finally {
                if (id === this.refreshId) this.timer = setTimeout(() => this.refresh(), 3000);
            }
        }
        async sendTest() {
            if (this.button.disabled || this.busy) return;
            this.busy = true;
            this.refreshId++;
            this.button.disabled = true;
            this.button.textContent = '전송 중…';
            clearTimeout(this.timer);
            try {
                await this.request(this.element.dataset.testUrl, 'POST');
                this.busy = false;
                await this.refresh();
            } catch (_) {
                this.busy = false;
                this.text('[data-error]', '요청 결과를 확인할 수 없습니다. 텔레그램에서 수신 여부를 확인해주세요.');
                this.button.textContent = '테스트 알림';
                this.timer = setTimeout(() => this.refresh(), 5000);
            }
        }
    }
    document.querySelectorAll('[data-service]').forEach(element => new NotificationSettings(element));
})();
