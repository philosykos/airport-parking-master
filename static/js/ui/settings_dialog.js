// 설정 레이어 팝업. 열려 있는 동안만 서비스별 알림 상태를 3초마다 다시 읽는다.
(() => {
    'use strict';
    const UI = window.UI = window.UI || {};

    class NotificationService {
        constructor(element, owner) {
            this.element = element;
            this.owner = owner;
            this.button = element.querySelector('[data-test]');
            this.busy = false;
            this.refreshId = 0;
            this.timer = null;
            this.canTest = false;
            this.button.addEventListener('click', () => this.sendTest());
        }
        text(selector, value) {
            const node = this.element.querySelector(selector);
            node.textContent = value;
            if (selector !== '[data-connection]') node.hidden = !value;
        }
        render(status) {
            this.owner.reportTelegramStatus(this, status.enabled);
            this.text('[data-connection]', status.credentialsConfigured ? '설정 완료' : '미설정');
            const label = result => UI.notificationStatus.label(result.status);
            this.text('[data-delivery]', status.lastDelivery ? `최근 알림: ${label(status.lastDelivery)}` : status.lastTest ? '' : '전송 내역 없음');
            this.text('[data-test-result]', status.lastTest ? `테스트: ${label(status.lastTest)}` : '');
            this.text('[data-error]', status.lastTest?.error || status.lastDelivery?.error || '');
            this.text('[data-test-help]', !status.enabled ? '알림을 켜면 테스트할 수 있습니다.' :
                !status.credentialsConfigured ? '봇 토큰과 수신자 ID를 설정해주세요.' : '');
            this.canTest = status.configured && !status.testPending;
            this.button.disabled = this.busy || !this.canTest;
            this.button.textContent = this.busy || status.testPending ? '전송 중…' : '테스트 알림';
        }
        stop() {
            clearTimeout(this.timer);
            this.timer = null;
            this.refreshId++;
        }
        schedule(delay) {
            if (this.owner.isOpen()) this.timer = setTimeout(() => this.refresh(), delay);
        }
        async refresh() {
            clearTimeout(this.timer);
            const id = ++this.refreshId;
            try {
                const status = await UI.api(this.element.dataset.statusUrl);
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
                if (id === this.refreshId) this.schedule(3000);
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
                await UI.api(this.element.dataset.testUrl, {method: 'POST', data: {}});
                this.busy = false;
                // 전송을 기다리는 사이 팝업을 닫았으면 상태를 다시 읽지 않는다.
                if (this.owner.isOpen()) await this.refresh();
            } catch (_) {
                this.busy = false;
                this.text('[data-error]', '요청 결과를 확인할 수 없습니다. 텔레그램에서 수신 여부를 확인해주세요.');
                this.button.textContent = '테스트 알림';
                // 실패 응답이 왔을 때 마지막으로 확인된 구성 상태가 허용하면 곧바로 다시 누를 수 있게 한다.
                this.button.disabled = !this.canTest;
                this.schedule(5000);
            }
        }
    }

    class SettingsDialog {
        constructor(dialog) {
            this.dialog = dialog;
            this.services = [...dialog.querySelectorAll('[data-service]')].map(element => new NotificationService(element, this));
            this.telegramStatuses = this.services.map(() => null);
            document.getElementById('open-settings')?.addEventListener('click', () => this.open());
            dialog.querySelectorAll('[data-settings-close]').forEach(button => button.addEventListener('click', () => this.close()));
            dialog.addEventListener('click', event => { if (event.target === dialog) this.close(); });
            dialog.addEventListener('close', () => {
                this.services.forEach(service => service.stop());
                UI.layers.unlock();
                // 닫으면 연 버튼으로 포커스를 돌려준다(브라우저 기본 동작에 기대지 않는다).
                if (this.trigger && document.contains(this.trigger)) this.trigger.focus({preventScroll: true});
                this.trigger = null;
            });
            const params = new URLSearchParams(location.search);
            if (params.get('settings') === '1') {
                params.delete('settings');
                const query = params.toString();
                history.replaceState(null, '', location.pathname + (query ? '?' + query : '') + location.hash);
                this.open();
            }
        }
        isOpen() { return this.dialog.open; }
        open() {
            if (this.dialog.open) return;
            this.trigger = document.activeElement;
            this.dialog.showModal();
            UI.layers.lock();
            // 새로고침 주기마다 서비스별 상태를 모두 모은 뒤 배지를 한 번만 쓴다.
            this.telegramStatuses = this.services.map(() => null);
            this.services.forEach(service => service.refresh());
        }
        close() {
            if (this.dialog.open) this.dialog.close();
        }
        reportTelegramStatus(service, enabled) {
            const index = this.services.indexOf(service);
            if (index < 0) return;
            this.telegramStatuses[index] = enabled;
            if (this.telegramStatuses.some(value => value === null)) return;
            // 기존 표시 규칙: 모든 서비스가 켜져 있어야 "켜짐"으로 보인다.
            this.setTelegramEnabled(this.telegramStatuses.every(Boolean));
        }
        setTelegramEnabled(enabled) {
            const badge = document.getElementById('telegram-state');
            badge.textContent = enabled ? '켜짐' : '꺼짐';
            badge.classList.toggle('is-on', enabled);
        }
    }

    UI.SettingsDialog = SettingsDialog;
    document.addEventListener('DOMContentLoaded', () => {
        const dialog = document.getElementById('settings-dialog');
        if (dialog) UI.settings = new SettingsDialog(dialog);
    });
})();
