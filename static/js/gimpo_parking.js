/* The form owns presentation only; job state always comes from the server. */
(() => {
    'use strict';
    const WallDate = window.VanillaPicker.WallClockDate;
    const $ = id => document.getElementById(id);
    const protectedStates = new Set(['PAYMENT_DISPATCHING', 'PAYMENT_IN_PROGRESS', 'PAYMENT_RESULT_UNKNOWN', 'RESERVED', 'PAYMENT_FAILED']);
    const labels = {CHECKING: '조회 중', WAITING_AVAILABLE: '만차 · 조회 대기', AVAILABLE: '예약 가능',
        PREPARING: '예약 정보 입력 중', PREPARED: '예약 정보 확인', RECHECKING: '최종 확인 중',
        PAYMENT_CONFIRM_READY: '결제 대기', PAYMENT_DISPATCHING: '결제 요청 중', PAYMENT_IN_PROGRESS: '결제 진행 중',
        PAYMENT_RESULT_UNKNOWN: '예약 결과 확인 필요', STOPPING: '중지 중', STOPPED: '중지됨', REVIEW_REQUIRED: '확인 필요',
        HANDOFF_CANCELLED: '결제 진행 취소', HANDOFF_EXPIRED: '결제 대기 시간 초과', SESSION_EXPIRED: '예약창 연결 종료',
        INTERRUPTED: '실행 중단', ERROR: '오류', CLOSED_BY_USER: '종료됨'};
    class GimpoApi {
        async call(path, method = 'GET', data) {
            const response = await fetch('/gimpo-parking/api' + path, {method,
                headers: data === undefined ? {} : {'Content-Type': 'application/json'},
                body: data === undefined ? undefined : JSON.stringify(data), cache: 'no-store'});
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || '요청을 처리하지 못했습니다.');
            return result;
        }
    }
    class ReservationScreen {
        constructor() { this.api = new GimpoApi(); this.job = null; this.connected = false; this.busy = false; this.events = []; this.pickers = {}; }
        async init() {
            this.bind();
            this.api.call('/reservation-password', 'POST', {}).then(data => {
                for (const input of document.querySelectorAll('.password-field input')) input.value = data.reservationPassword;
                for (const button of document.querySelectorAll('.password-field button')) button.disabled = false;
            }).catch(error => {
                for (const input of document.querySelectorAll('.password-field input')) input.placeholder = '비밀번호 설정을 확인해주세요';
                this.message(error.message);
            });
            try {
                const options = await this.api.call('/options');
                this.configure(options);
                const saved = await this.api.call('/defaults');
                this.apply(Object.fromEntries(['airportCode', 'parkingId', 'carNumber', 'phone', 'discountSelection', 'intervalSeconds'].filter(key => key in saved).map(key => [key, saved[key]])));
                const existing = await this.api.call('/jobs/active');
                this.connected = true;
                this.setJob(existing.job || existing.recent);
                if (this.job) this.apply(this.job.inputs);
                this.render();
            } catch (error) { this.message(error.message); }
            this.poll();
        }
        configure(options) {
            this.options = options;
            for (const [id, values] of [['airportCode', options.airports], ['parkingId', options.parkingLots], ['discountSelection', options.discounts]]) {
                $(id).replaceChildren(...values.map(v => new Option(v.label, v.value)));
            }
            $('intervalSeconds').value = options.intervalSeconds;
            this.updatePolicy(options.policy);
            const min = this.parse(options.policy.entryMin);
            for (const [id, container, date] of [['entryAt', 'entry-picker', min], ['exitAt', 'exit-picker', new WallDate(min.getTime() + 7200000)]]) {
                this.pickers[id] = new window.VanillaPicker($(container), {defaultDate: date, wallClock: true,
                    minDate: min, maxDate: this.parse(options.policy.exitMax), now: () => this.parse(this.options.policy.now.slice(0, 16).replace('T', ' ')),
                    validate: d => d.getMinutes() % 10 === 0 && d >= this.parse(this.options.policy.entryMin) && d <= this.parse(this.options.policy.exitMax)});
            }
            for (const [id, container] of [['entryAt', 'entry-picker'], ['exitAt', 'exit-picker']]) {
                window.createScrollTimePicker(this.pickers[id], container, 10);
                $(container).addEventListener('vp.show', () => {
                    for (const [otherId, picker] of Object.entries(this.pickers)) {
                        if (otherId !== id) picker.hide();
                    }
                });
                document.addEventListener('click', event => {
                    const picker = this.pickers[id];
                    if (!$(container).contains(event.target) && !picker._widget.contains(event.target)) picker.hide();
                });
            }
            $('entry-picker').addEventListener('vp.change', () => this.updateExitLimits());
            $('entryAt').addEventListener('change', () => this.updateExitLimits());
            this.updateExitLimits();
        }
        updatePolicy(policy) {
            this.options.policy = policy;
            this.pickers.entryAt?.setLimits(this.parse(policy.entryMin), this.parse(policy.exitMax));
            this.updateExitLimits();
        }
        updateExitLimits() {
            if (!this.pickers.exitAt) return;
            const start = this.parse($('entryAt').value) || this.parse(this.options.policy.entryMin);
            const maximum = Math.min(start.getTime() + 30 * 86400000, this.parse(this.options.policy.exitMax).getTime());
            this.pickers.exitAt.setLimits(new WallDate(start.getTime() + 7200000), new WallDate(maximum),
                d => d.getMinutes() % 10 === 0 && d.getTime() >= start.getTime() + 7200000 && d.getTime() <= maximum);
        }
        parse(value) {
            const m = String(value).match(/^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})$/);
            if (!m) return null;
            const d = new WallDate(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
            return d.getFullYear() === +m[1] && d.getMonth() === +m[2] - 1 && d.getDate() === +m[3] && d.getHours() === +m[4] && d.getMinutes() === +m[5] ? d : null;
        }
        message(text) { $('message').textContent = text; }
        inputs(mode) {
            const data = Object.fromEntries(new FormData($('reservation-form')));
            data.intervalSeconds = Number(data.intervalSeconds);
            data.mode = mode;
            data.agreements = Object.fromEntries(['agree01', 'agree03', 'agree04', 'agree05'].map(k => [k, true]));
            data.autoProceedConsent = mode === 'watch';
            return data;
        }
        apply(data) {
            for (const [key, value] of Object.entries(data || {})) {
                if (['reservationPassword', 'passwordConfirmation', 'agreements'].includes(key)) continue;
                if ($(key) && $(key).type !== 'checkbox') $(key).value = value;
                if (this.pickers[key] && this.parse(value)) this.pickers[key].dates.setValue(this.parse(value));
            }
            this.updateExitLimits();
        }
        async saveDefaults() {
            const data = this.inputs('once');
            const saved = Object.fromEntries(['airportCode', 'parkingId', 'carNumber', 'phone', 'discountSelection', 'intervalSeconds'].map(key => [key, data[key]]));
            this.saveQueue = (this.saveQueue || Promise.resolve()).catch(() => {}).then(() => this.api.call('/defaults', 'POST', saved));
            await this.saveQueue;
        }
        version() { return {inputVersion: this.job.inputVersion, generation: this.job.generation}; }
        setJob(job) {
            if (job && this.job?.id === job.id && job.stateVersion < this.job.stateVersion) return false;
            if (this.job?.id !== job?.id) {
                this.events = [];
                this.browserAvailable = false;
            }
            this.job = job;
            return true;
        }
        async perform(action) {
            if (this.busy) return;
            this.busy = true; this.render();
            try { const result = await action(); this.message(result?.message || ''); }
            catch (error) { this.message(error.message); }
            finally { this.busy = false; this.render(); }
        }
        async start(mode) {
            const options = await this.api.call('/options');
            this.updatePolicy(options.policy);
            if (!$('reservation-form').reportValidity()) return;
            await this.saveDefaults();
            const inputs = this.inputs(mode);
            const result = await this.api.call('/jobs', 'POST', inputs);
            this.setJob(result.job);
        }
        async command(action, extra = {}) {
            const result = await this.api.call(`/jobs/${this.job.id}/${action}`, 'POST', {...this.version(), ...extra});
            if (result.job) this.setJob(result.job);
        }
        bind() {
            // Remove obsolete controls from templates cached by a running server.
            for (const id of ['job-id', 'reason', 'reprepare']) $(id)?.remove();
            for (const [buttonId, inputId, label] of [
                ['toggle-password', 'reservationPassword', '비밀번호'],
                ['toggle-password-confirmation', 'passwordConfirmation', '확인 비밀번호']
            ]) {
                $(buttonId).onclick = () => {
                    const visible = $(inputId).type === 'password';
                    $(inputId).type = visible ? 'text' : 'password';
                    $(buttonId).setAttribute('aria-pressed', String(visible));
                    $(buttonId).setAttribute('aria-label', `${label} ${visible ? '숨기기' : '보기'}`);
                    $(buttonId).querySelector('span').textContent = visible ? 'visibility_off' : 'visibility';
                };
            }
            $('reservation-form').addEventListener('submit', event => { event.preventDefault(); this.perform(() => this.start('once')); });
            $('reservation-form').addEventListener('change', () => {
                if (this.connected && !this.job?.active) this.saveDefaults().catch(error => this.message(error.message));
            });
            $('watch').onclick = () => this.perform(() => this.start('watch'));
            for (const action of ['stop', 'prepare', 'show-browser']) $(action).onclick = () => this.perform(() => this.command(action));
            $('proceed').onclick = () => this.perform(() => this.command('proceed', {autoProceedConsent: true}));
            $('resolve').onclick = () => this.perform(() => this.command('resolve', {outcome: $('outcome').value, acknowledged: $('resolve-consent').checked}));
            $('resend').onclick = () => this.perform(async () => {
                const event = this.latestEvent;
                await this.api.call(`/jobs/${this.job.id}/notifications/resend`, 'POST', {...this.version(), eventId: event.id, round: event.round});
            });
            $('import-t2').onclick = () => this.perform(async () => {
                const response = await fetch('/t2-valet/api/defaults', {cache: 'no-store'});
                if (!response.ok) throw new Error('T2 저장 정보를 불러오지 못했습니다.');
                const data = await response.json(); this.apply({carNumber: data.carNumber || '', phone: data.phone || ''});
                await this.saveDefaults();
            });
            document.addEventListener('click', e => {
                if (!e.target.closest('.td-input-group') && !e.target.closest('.tempus-dominus-widget')) Object.values(this.pickers).forEach(p => p.hide());
            });
        }
        render() {
            const job = this.job, active = !!job?.active, state = job?.state;
            this.latestEvent = this.events.filter(e => e.status !== 'CANCELLED').at(-1);
            const event = this.latestEvent;
            $('job-notification').hidden = !event;
            $('notification-status').textContent = event ? `${event.kind === 'CORRECTION' ? '변경 안내' : '결제 안내'}: ${window.NotificationStatus.label(event.status)}${event.error ? ': ' + event.error : ''}` : '';
            $('resend').hidden = !event || !['FAILED', 'UNKNOWN', 'SENT'].includes(event.status) || (event.kind === 'READY' && state !== 'PAYMENT_CONFIRM_READY');
            $('resend-help').hidden = $('resend').hidden;
            $('input-fields').disabled = !this.connected || active;
            $('check').disabled = $('watch').disabled = !this.connected || active || this.busy;
            $('import-t2').disabled = active || this.busy;
            $('state').textContent = job ? labels[state] || '상태 확인 필요' : (this.connected ? '대기 중' : '연결 중');
            for (const [id, show] of Object.entries({prepare: state === 'AVAILABLE', proceed: state === 'PREPARED',
                stop: active && !protectedStates.has(state) && !['STOPPING', 'CLOSED_BY_USER'].includes(state),
                'show-browser': active && this.browserAvailable, resolution: protectedStates.has(state) || (active && state === 'CLOSED_BY_USER')})) $(id).hidden = !show;
            document.querySelectorAll('#job-actions button, #resolve').forEach(b => b.disabled = this.busy);
            $('job-actions').hidden = !Array.from($('job-actions').children).some(button => !button.hidden);
            $('summary').replaceChildren();
            $('summary').hidden = !job?.summary;
            const values = job?.summary;
            if (values) for (const [key, label] of [['parkingName', '주차장'], ['entryAt', '입차'], ['exitAt', '출차'], ['calculateAmt', '예상 주차요금'], ['depositAmt', '예약 보증금']]) {
                const dt = document.createElement('dt'), dd = document.createElement('dd');
                dt.textContent = label; dd.textContent = typeof values[key] === 'number' ? values[key].toLocaleString() + '원' : values[key];
                $('summary').append(dt, dd);
            }
            const formatTime = timestamp => new Date(timestamp * 1000).toLocaleString('ko-KR', {timeZone: 'Asia/Seoul'});
            $('freshness').hidden = state !== 'PAYMENT_CONFIRM_READY';
            $('freshness').textContent = state === 'PAYMENT_CONFIRM_READY' ? `${formatTime(job.handoffDeadline)}까지 결제를 진행해주세요. 자리는 확보되지 않았습니다.` : '';
            const logKey = job ? `${job.id}:${job.stateVersion}` : '';
            if (logKey !== this.logKey) {
                this.logKey = logKey;
                $('logs').replaceChildren(...(job?.logs || []).slice().reverse().map(log => { const li = document.createElement('li'); li.textContent = `${formatTime(log.time)} · ${log.message}`; return li; }));
            }
        }
        async poll() {
            try {
                if (!this.connected) {
                    const existing = await this.api.call('/jobs/active');
                    this.connected = true; this.setJob(existing.job || existing.recent);
                }
                if (this.job) {
                    const jobId = this.job.id;
                    const status = await this.api.call(`/jobs/${jobId}`);
                    if (this.job?.id === jobId && this.setJob(status.job)) {
                        this.events = status.notifications;
                        this.browserAvailable = status.browserAvailable;
                    }
                }
                this.render();
            } catch (error) { this.message(error.message); }
            setTimeout(() => this.poll(), 1500);
        }
    }
    new ReservationScreen().init();
})();
