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
    const TONES = {
        running: ['CHECKING', 'PREPARING', 'RECHECKING', 'PAYMENT_DISPATCHING', 'PAYMENT_IN_PROGRESS', 'STOPPING'],
        idle: ['WAITING_AVAILABLE', 'STOPPED'],
        success: ['AVAILABLE', 'RESERVED'],
        warning: ['PREPARED', 'PAYMENT_CONFIRM_READY', 'PAYMENT_RESULT_UNKNOWN', 'REVIEW_REQUIRED', 'HANDOFF_CANCELLED',
            'HANDOFF_EXPIRED', 'SESSION_EXPIRED', 'INTERRUPTED'],
        error: ['ERROR', 'PAYMENT_FAILED'],
    };
    const toneOf = (state, outcome) => state === 'CLOSED_BY_USER' ? (outcome === 'reserved' ? 'success' : 'idle')
        : Object.keys(TONES).find(tone => TONES[tone].includes(state)) || 'idle';
    const formatTime = timestamp => new Date(timestamp * 1000).toLocaleString('ko-KR', {timeZone: 'Asia/Seoul'});
    class GimpoApi {
        call(path, method = 'GET', data) {
            return UI.api('/gimpo-parking/api' + path, {method, data});
        }
    }
    class ReservationScreen {
        constructor() { this.api = new GimpoApi(); this.job = null; this.connected = false; this.busy = false; this.events = []; this.pickers = {};
            this.logPanel = new UI.LogPanel($('log-panel'));
            new UI.SelectPicker($('select-picker-overlay')).attach(document.querySelector('.form-panel')); }
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
                if (!existing.job?.active) this.restoreDates('entryAt' in saved || 'exitAt' in saved ? saved : existing.recent?.inputs);
                this.connected = true;
                this.setJob(existing.job || existing.recent);
                if (this.job?.active) this.apply(this.job.inputs);
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
        message(text, type = 'error') { if (text) UI.toast(text, type); }
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
        restoreDates(data) {
            if (!data) return;
            const entry = this.parse(data.entryAt), exit = this.parse(data.exitAt);
            if (!entry || !exit || entry.getMinutes() % 10 || exit.getMinutes() % 10
                || entry < this.parse(this.options.policy.entryMin)
                || exit > this.parse(this.options.policy.exitMax)
                || exit - entry < 7200000 || exit - entry > 30 * 86400000) return;
            this.apply({entryAt: data.entryAt, exitAt: data.exitAt});
        }
        async saveDefaults() {
            const data = this.inputs('once');
            const saved = Object.fromEntries(['airportCode', 'parkingId', 'carNumber', 'phone', 'discountSelection', 'intervalSeconds', 'entryAt', 'exitAt'].map(key => [key, data[key]]));
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
            try { const result = await action(); this.message(result?.message || '', 'success'); }
            catch (error) { this.message(error.message); }
            finally { this.busy = false; this.render(); }
        }
        validateFields() {
            const invalid = [...$('reservation-form').querySelectorAll('input[required], select[required]')].filter(input => !input.checkValidity());
            document.querySelectorAll('#reservation-form .field-group.has-error').forEach(group => group.classList.remove('has-error'));
            invalid.forEach(input => input.closest('.field-group')?.classList.add('has-error'));
            if (!invalid.length) return true;
            invalid[0].focus();
            UI.toast(`${invalid.length}개 항목을 확인해주세요`, 'error');
            return false;
        }
        async start(mode) {
            const options = await this.api.call('/options');
            this.updatePolicy(options.policy);
            if (!this.validateFields()) return;
            await this.saveDefaults();
            const inputs = this.inputs(mode);
            const result = await this.api.call('/jobs', 'POST', inputs);
            this.setJob(result.job);
            this.logPanel.openSheet();
        }
        async command(action, extra = {}) {
            const result = await this.api.call(`/jobs/${this.job.id}/${action}`, 'POST', {...this.version(), ...extra});
            if (result.job) this.setJob(result.job);
            return result;
        }
        bind() {
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
            const saveChanges = () => {
                if (this.connected && !this.job?.active) this.saveDefaults().catch(error => this.message(error.message));
            };
            $('reservation-form').addEventListener('input', event => event.target.closest('.field-group')?.classList.remove('has-error'));
            $('reservation-form').addEventListener('change', saveChanges);
            $('reservation-form').addEventListener('vp.change', saveChanges);
            $('watch').onclick = () => this.perform(() => this.start('watch'));
            for (const action of ['stop', 'prepare', 'show-browser']) $(action).onclick = () => this.perform(() => this.command(action));
            $('proceed').onclick = () => this.perform(() => this.command('proceed', {autoProceedConsent: true}));
            $('resolve').onclick = () => this.perform(() => this.command('resolve', {outcome: $('outcome').value, acknowledged: $('resolve-consent').checked}));
            $('resend').onclick = () => this.perform(async () => {
                const event = this.latestEvent;
                await this.api.call(`/jobs/${this.job.id}/notifications/resend`, 'POST', {...this.version(), eventId: event.id, round: event.round});
            });
            $('import-t2').onclick = () => this.perform(async () => {
                const data = await UI.api('/t2-valet/api/defaults');
                this.apply({carNumber: data.carNumber || '', phone: data.phone || ''});
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
            $('stop').disabled = this.busy || !(active && !protectedStates.has(state) && !['STOPPING', 'CLOSED_BY_USER'].includes(state));
            const label = job ? labels[state] || '상태 확인 필요' : (this.connected ? '대기 중' : '연결 중');
            const tone = job ? toneOf(state, job.userReportedOutcome) : 'idle';
            $('state').textContent = label;
            $('state').dataset.tone = tone;
            UI.statusBadge.set({label, tone});
            document.querySelector('.form-panel').classList.toggle('form-panel--active', active);
            for (const [id, show] of Object.entries({prepare: state === 'AVAILABLE', proceed: state === 'PREPARED',
                'show-browser': active && this.browserAvailable, resolution: protectedStates.has(state) || (active && state === 'CLOSED_BY_USER')})) $(id).hidden = !show;
            document.querySelectorAll('#job-actions button, #resolve').forEach(b => b.disabled = this.busy);
            $('job-actions').hidden = !Array.from($('job-actions').children).some(button => !button.hidden);
            $('summary').replaceChildren();
            $('summary').hidden = !job?.summary;
            // 예상 주차요금은 할인 후 금액이다(기존 동작 유지, test_summary_displays_discounted_price).
            const values = job?.summary ? {...job.summary, estimatedAmt: job.summary.calculateAmt - (job.summary.discountAmt || 0)} : null;
            if (values) for (const [key, name] of [['parkingName', '주차장'], ['entryAt', '입차'], ['exitAt', '출차'], ['estimatedAmt', '예상 주차요금'], ['depositAmt', '예약 보증금']]) {
                const dt = document.createElement('dt'), dd = document.createElement('dd');
                dt.textContent = name; dd.textContent = typeof values[key] === 'number' ? values[key].toLocaleString() + '원' : values[key];
                $('summary').append(dt, dd);
            }
            $('freshness').hidden = state !== 'PAYMENT_CONFIRM_READY';
            $('freshness').textContent = state === 'PAYMENT_CONFIRM_READY' ? `${formatTime(job.handoffDeadline)}까지 결제를 진행해주세요. 자리는 확보되지 않았습니다.` : '';
            const logKey = job ? `${job.id}:${job.stateVersion}` : '';
            if (logKey !== this.logKey) {
                this.logKey = logKey;
                this.logPanel.render((job?.logs || []).map(log => this.toEntry(log, job)));
            }
        }
        toEntry(log, job) {
            const name = labels[log.state] || log.state;
            return {time: formatTime(log.time),
                type: job.inputs?.mode === 'watch' ? {label: '자동 예약', variant: 'schedule'} : {label: '1회 조회', variant: 'test'},
                status: {label: name, tone: toneOf(log.state, job.userReportedOutcome)},
                summary: log.message,
                detail: [{title: '상태', kind: 'text', body: `${name} (${log.state})`}, {title: '메시지', kind: 'text', body: log.message}]};
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
                this.lastPollError = null;
            } catch (error) {
                UI.statusBadge.set({label: '연결 끊김', tone: 'error'});
                if (error.message !== this.lastPollError) {
                    this.lastPollError = error.message;
                    this.message(error.message);
                }
            }
            setTimeout(() => this.poll(), 1500);
        }
    }
    new ReservationScreen().init();
})();
