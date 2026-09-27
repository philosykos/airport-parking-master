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
    // 로그 시간은 T2와 같은 yyyy-MM-dd HH:mm:ss 형식이다(sv-SE 로캘이 이 형식을 낸다).
    const formatLogTime = timestamp => new Date(timestamp * 1000).toLocaleString('sv-SE', {timeZone: 'Asia/Seoul'});
    // 완료 오버레이를 한 번만 띄우기 위한 기록. 같은 작업이라도 다시 조회(generation)하거나
    // 결제 대기에 다시 도달(handoffEpoch)하면 새 키가 된다. 저장소를 못 쓰면 메모리에만 둔다.
    class CompletionMemory {
        constructor() { this.memory = new Set(); }
        prefix(job) { return `gimpo.completion.${job.id}.${job.generation}.${job.handoffEpoch}.`; }
        key(job, step) { return this.prefix(job) + step; }
        // 지금 작업의 기록만 남기고 지난 작업·세대·결제 대기 회차의 기록을 지운다(쌓이지 않게).
        prune(job) {
            const keep = this.prefix(job);
            try {
                const storage = window.localStorage;
                const stale = [];
                for (let i = 0; i < storage.length; i++) {
                    const key = storage.key(i);
                    if (key?.startsWith('gimpo.completion.') && !key.startsWith(keep)) stale.push(key);
                }
                stale.forEach(key => storage.removeItem(key));
            } catch (_) { /* 저장소를 못 쓰면 지울 것도 없다 */ }
        }
        has(job, step) {
            const key = this.key(job, step);
            if (this.memory.has(key)) return true;
            try { return window.localStorage.getItem(key) === '1'; } catch (_) { return false; }
        }
        remember(job, step, persist = true) {
            const key = this.key(job, step);
            this.memory.add(key);
            if (!persist) return;
            try { window.localStorage.setItem(key, '1'); } catch (_) { /* 메모리 기록만 남긴다 */ }
        }
        forget(job, step) {
            const key = this.key(job, step);
            this.memory.delete(key);
            try { window.localStorage.removeItem(key); } catch (_) { /* 메모리 기록만 지운다 */ }
        }
    }
    class GimpoApi {
        call(path, method = 'GET', data) {
            return UI.api('/gimpo-parking/api' + path, {method, data});
        }
    }
    class ReservationScreen {
        constructor() { this.api = new GimpoApi(); this.job = null; this.connected = false; this.busy = false; this.events = []; this.pickers = {};
            this.completion = new CompletionMemory(); this.shownKey = null; this.activeJobIds = new Set();
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
            // 작업 식별자(작업·세대·결제 대기 회차)가 바뀔 때 한 번 지난 완료 기록을 지운다. 작업이 없으면 두지 않는다.
            const prefix = job ? this.completion.prefix(job) : null;
            if (prefix && prefix !== this.prunedPrefix) {
                this.prunedPrefix = prefix;
                this.completion.prune(job);
            }
            return true;
        }
        async perform(action) {
            if (this.busy) return;
            this.busy = true; this.render();
            try {
                const result = await action();
                // 명령이 성공했다는 것은 서버와 다시 통신되고 있다는 뜻이다. 실패한 명령은 여기 오지 않는다(catch로 간다).
                this.pollFailed = false;
                this.message(result?.message || '', 'success');
            }
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
            // 서버가 이 요청에 응답했다는 것은 다시 연결되어 있다는 뜻이다. 다음 폴링을 기다리지 않고 바로 반영한다.
            this.pollFailed = false;
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
            $('reservation-form').addEventListener('vp.change', event => {
                event.target.closest('.field-group')?.classList.remove('has-error');
                saveChanges();
            });
            $('watch').onclick = () => this.perform(() => this.start('watch'));
            for (const action of ['stop', 'prepare', 'show-browser']) $(action).onclick = () => this.perform(() => this.command(action));
            $('proceed').onclick = () => this.perform(() => this.command('proceed', {autoProceedConsent: true}));
            $('record-result').onclick = () => { if (this.job) this.openResultPrompt(this.job, false); };
            $('resend').onclick = () => this.perform(async () => {
                const event = this.latestEvent;
                await this.api.call(`/jobs/${this.job.id}/notifications/resend`, 'POST', {...this.version(), eventId: event.id, round: event.round});
            });
            $('import-t2').onclick = () => this.perform(async () => {
                let data;
                try { data = await UI.api('/t2-valet/api/defaults'); }
                catch (error) { throw new Error(error.data?.error || 'T2 저장 정보를 불러오지 못했습니다.'); }
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
            // 폴링이 실패한 뒤로는 다음 폴링이 성공할 때까지 어느 렌더든 연결 끊김을 보인다.
            const label = this.pollFailed ? '연결 끊김' : job ? labels[state] || '상태 확인 필요' : (this.connected ? '대기 중' : '연결 중');
            const tone = this.pollFailed ? 'error' : job ? toneOf(state, job.userReportedOutcome) : 'idle';
            UI.statusBadge.set({label, tone});
            document.querySelector('.form-panel').classList.toggle('form-panel--active', active);
            for (const [id, show] of Object.entries({prepare: state === 'AVAILABLE', proceed: state === 'PREPARED',
                'show-browser': active && this.browserAvailable, 'record-result': this.canRecordResult(job)})) $(id).hidden = !show;
            document.querySelectorAll('#job-actions button').forEach(b => b.disabled = this.busy);
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
            $('progress-card').hidden = $('summary').hidden && $('freshness').hidden && $('job-actions').hidden && $('job-notification').hidden;
            const logKey = job ? `${job.id}:${job.stateVersion}` : '';
            if (logKey !== this.logKey) {
                this.logKey = logKey;
                this.logPanel.render((job?.logs || []).map(log => this.toEntry(log, job)));
            }
            this.updateCompletion();
        }
        toEntry(log, job) {
            const name = labels[log.state] || log.state;
            return {time: formatLogTime(log.time),
                type: job.inputs?.mode === 'watch' ? {label: '자동 예약', variant: 'schedule'} : {label: '1회 조회', variant: 'test'},
                status: {label: name, tone: toneOf(log.state, job.userReportedOutcome)},
                summary: log.message,
                detail: [{title: '상태', kind: 'text', body: `${name} (${log.state})`}, {title: '메시지', kind: 'text', body: log.message}]};
        }
        // 결과 기록 버튼(#record-result)을 보이는 조건. busy 뒤 결과 창을 다시 열 때도 같은 조건을 쓴다.
        canRecordResult(job) {
            return !!job && (protectedStates.has(job.state) || (job.active && job.state === 'CLOSED_BY_USER'));
        }
        completionStep(job) {
            if (!job.active) return job.state === 'CLOSED_BY_USER' && job.userReportedOutcome === 'reserved' ? 'success' : null;
            if (job.state === 'PAYMENT_CONFIRM_READY') return 'action';
            if (job.state === 'PAYMENT_IN_PROGRESS' && job.returnedFromPayment) return 'result';
            return null;
        }
        updateCompletion() {
            const job = this.job;
            // 명령 처리 중이거나 설정 팝업이 열려 있으면 다음 렌더에서 다시 판단한다.
            if (!job || !this.connected || this.busy || UI.settings?.isOpen()) return;
            if (job.active) this.activeJobIds.add(job.id);
            if (this.retryResultJobId) {
                // busy로 거절된, 사용자가 연 결과 기록 창을 busy가 풀린 뒤 다시 연다. 작업이 바뀌었거나 끝났으면 버린다.
                const retry = this.retryResultJobId === job.id && job.active && this.canRecordResult(job) && !UI.completion.isOpen();
                this.retryResultJobId = null;
                if (retry) { this.openResultPrompt(job, false); return; }
            }
            const step = this.completionStep(job);
            const key = step ? this.completion.key(job, step) : null;
            if (UI.completion.isOpen()) {
                // 자동으로 띄운 안내가 지금 단계와 맞으면 그대로 둔다. 사용자가 연 결과 기록 창(shownKey 없음)도 그대로 둔다.
                if (!this.shownKey || this.shownKey === key) return;
                UI.completion.close('replace');
            }
            this.shownKey = null;
            if (!step || this.completion.has(job, step)) return;
            this.shownKey = key;
            if (step === 'success') {
                this.completion.remember(job, 'success');
                // 이 페이지에서 진행 중인 모습을 본 작업만 축하한다. 다른 기기·브라우저로 연 지난 예약은 기록만 한다.
                if (!this.activeJobIds.has(job.id)) { this.shownKey = null; return; }
                UI.toast('예약 완료를 기록했습니다', 'success');
                UI.completion.success({title: '예약이', highlight: '완료되었습니다',
                    subtitle: '공항 사이트에서 확인한 결과를 기록했습니다. 예약 내역은 공식 사이트에서 다시 볼 수 있습니다.'});
            } else if (step === 'action') {
                const remember = () => this.completion.remember(job, 'action');
                UI.completion.action({icon: 'payments', title: '공항 예약창에서 결제해주세요',
                    subtitle: `${formatTime(job.handoffDeadline)}까지 결제를 진행해주세요. 자리는 확보되지 않았습니다.`,
                    actions: [{label: '공항 예약창 보기', onClick: () => { remember(); this.perform(() => this.command('show-browser')); }},
                        {label: '닫기', variant: 'secondary', onClick: remember}],
                    onDismiss: remember});
            } else {
                this.openResultPrompt(job, true);
            }
        }
        openResultPrompt(job, returned) {
            if (!returned) this.shownKey = null;  // 사용자가 연 창은 상태 변화로 닫지 않는다
            // '아직 결제 중'이나 Esc는 이 페이지에서만 기억한다(새로고침하면 다시 뜬다).
            const later = () => this.completion.remember(job, 'result', false);
            const send = async outcome => {
                if (this.busy || this.job?.id !== job.id) {
                    UI.toast('다른 요청을 처리하고 있습니다. 잠시 후 다시 선택해주세요.', 'info');
                    // 결제 복귀 안내는 기록이 없으니 다음 렌더가 다시 띄운다. 사용자가 연 창은 여기서 다시 열 차례를 남긴다.
                    if (!returned && this.job?.id === job.id) this.retryResultJobId = job.id;
                    return;
                }
                // 요청이 끝날 때까지는 메모리에만 기억하고, 서버가 받아들인 뒤에 저장한다. 실패하면 지워 다시 뜨게 한다.
                this.completion.remember(job, 'result', false);
                this.busy = true;
                this.render();
                try {
                    await this.command('resolve', {outcome, acknowledged: true});
                    this.completion.remember(job, 'result');
                } catch (error) {
                    this.completion.forget(job, 'result');
                    UI.toast(error.message, 'error');
                } finally {
                    this.busy = false;
                    this.render();
                }
            };
            UI.completion.action({icon: 'fact_check',
                title: returned ? '결제창에서 공항 사이트로 돌아왔습니다' : '예약 결과를 기록해주세요',
                subtitle: '결제가 끝났는지는 앱이 판단하지 못합니다. 공항 사이트에서 예약 내역을 확인한 뒤 결과를 선택해주세요. 결과를 선택하면 예약창을 닫습니다.',
                actions: [{label: '예약 완료', onClick: () => send('reserved')},
                    {label: '예약 안 됨', variant: 'secondary', onClick: () => send('not_reserved')},
                    {label: '확인 못함', variant: 'secondary', onClick: () => send('unknown')},
                    {label: '아직 결제 중', variant: 'secondary', onClick: later}],
                onDismiss: later});
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
                this.pollFailed = false;
                this.render();
                this.lastPollError = null;
            } catch (error) {
                // 헤더가 연결 끊김을 보인다. 다음 폴링이 성공해야 풀린다(render()가 이 표시를 따른다).
                this.pollFailed = true;
                UI.statusBadge.set({label: '연결 끊김', tone: 'error'});
                if (error.message !== this.lastPollError) {
                    this.lastPollError = error.message;
                    this.message(error.message);
                }
            }
            setTimeout(() => this.poll(), 1500);
        }
    }
    window.gimpoScreen = new ReservationScreen();
    window.gimpoScreen.init();
})();
