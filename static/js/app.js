// T2 화면 컨트롤러. 공통 모듈(window.UI)에 표시를 맡기고, 입력 검증·서버 호출·로그 변환만 한다.
(() => {
    'use strict';
    const API = '/t2-valet/api';
    const $ = id => document.getElementById(id);
    // 서버 services/t2/validation.py FIELDS와 같은 목록·같은 형식 규칙을 쓴다.
    const FORM_FIELDS = ['name', 'phone', 'carNumber', 'carModel', 'carBrand', 'carColor', 'departingAt', 'arrivedAt', 'departingAir'];
    const DATE_FIELDS = ['departingAt', 'arrivedAt'];
    const FORMAT_RULES = {
        phone: {pattern: /^010\d{8}$/, message: '휴대전화 번호는 010으로 시작하는 숫자 11자리로 입력해주세요.'},
        carNumber: {pattern: /^\d{2,3}[가-힣]\d{4}$/, message: '차량번호 형식을 확인해주세요. 예: 12가3456, 123가4567'},
    };
    const TYPES = {test: ['1회 요청', 'test'], schedule: ['자동 예약', 'schedule'], call: ['요청', 'test'],
        event: ['상태', 'event'], notification: ['알림', 'event']};
    const NOTIFICATION_TONES = {SENT: 'success', FAILED: 'error', UNKNOWN: 'error', DISABLED: 'idle', CANCELLED: 'idle',
        PENDING: 'running', SENDING: 'running', RETRYING: 'running'};

    function toEntry(log) {
        const [label, variant] = TYPES[log.type] || ['기록', 'event'];
        let tone;
        if (log.type === 'event') tone = 'info';
        else if (log.type === 'notification') tone = NOTIFICATION_TONES[log.status] || 'idle';
        else tone = typeof log.status === 'number' && log.status >= 200 && log.status < 300 ? 'success' : 'error';
        const body = log.body == null ? '' : String(log.body);
        const detail = [];
        if (log.url) detail.push({title: 'REQUEST URL', kind: 'url', method: log.method || 'POST', body: log.url});
        if (log.payload && Object.keys(log.payload).length) detail.push({title: 'REQUEST PAYLOAD', kind: 'json', body: JSON.stringify(log.payload)});
        detail.push({title: log.url ? 'RESPONSE BODY' : '내용', kind: log.url ? 'json' : 'text', body});
        return {time: log.time, type: {label, variant}, status: {label: String(log.status ?? '—'), tone}, summary: body, detail};
    }

    // 스케줄이 성공하면 200 로그 뒤에 SUCCESS 이벤트가 붙으므로 뒤에서부터 찾는다.
    function latestSuccessId(logs) {
        for (let i = logs.length - 1; i >= 0; i--) {
            if (logs[i].status === 200) return logs[i].time + '-' + logs[i].type;
        }
        return null;
    }

    const toPickerDate = value => new Date(value.substring(0, 16).replace(' ', 'T'));

    class T2Screen {
        constructor() {
            this.pollTimer = null;
            this.fetchSeq = 0;
            this.running = false;
            this.savedDefaults = null;
            this.lastSuccessId = null;
            this.initialLogsFetched = false;
            this.logPanel = new UI.LogPanel($('log-panel'));
            new UI.SelectPicker($('select-picker-overlay')).attach(document.querySelector('.form-panel'));
            this.initPickers();
            this.bindFields();
            $('btn-start').addEventListener('click', () => this.start());
            $('btn-stop').addEventListener('click', () => this.stop());
            $('btn-test').addEventListener('click', () => this.testCall());
            $('btn-clear').addEventListener('click', () => this.clearLogs());
            this.updateUI(false);
            this.loadDefaults();
            this.fetchLogs();
        }

        initPickers() {
            const now = new Date();
            const departing = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 9, 0);
            const later = new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000);
            const arriving = new Date(later.getFullYear(), later.getMonth(), later.getDate(), 18, 0);
            this.tdDeparting = new VanillaPicker($('departingAtPicker'), {defaultDate: departing, format: 'yyyy-MM-dd HH:mm', locale: 'ko', stepping: 5});
            this.tdArrived = new VanillaPicker($('arrivedAtPicker'), {defaultDate: arriving, format: 'yyyy-MM-dd HH:mm', locale: 'ko', stepping: 5});
            DATE_FIELDS.forEach(id => $(id).classList.add('is-placeholder'));
            const syncRange = () => {
                this.tdDeparting.setRange(this.tdDeparting.dates.lastPicked, this.tdArrived.dates.lastPicked);
                this.tdArrived.setRange(this.tdDeparting.dates.lastPicked, this.tdArrived.dates.lastPicked);
            };
            syncRange();
            this.tdArrived.setMinDate(this.tdDeparting.dates.lastPicked);
            $('departingAtPicker').addEventListener('vp.change', () => {
                // 출발일시를 바꾸면 도착일시를 같은 날 18시로 되돌린다.
                const departure = this.tdDeparting.dates.lastPicked;
                const reset = new Date(departure);
                reset.setHours(18, 0, 0, 0);
                this.tdArrived.setMinDate(departure);
                this.tdArrived.dates.setValue(reset);
                $('arrivedAt').classList.add('is-placeholder');
                syncRange();
            });
            $('arrivedAtPicker').addEventListener('vp.change', syncRange);
            [['departingAtPicker', 'departingAt'], ['arrivedAtPicker', 'arrivedAt']].forEach(([pickerId, inputId]) => {
                $(pickerId).addEventListener(VanillaPickerNamespace.events.change, () => {
                    $(inputId).closest('.field-group')?.classList.remove('has-error');
                    $(inputId).classList.toggle('is-placeholder', !$(inputId).value);
                });
            });
            createScrollTimePicker(this.tdDeparting, 'departingAtPicker');
            createScrollTimePicker(this.tdArrived, 'arrivedAtPicker');
            $('departingAtPicker').addEventListener(VanillaPickerNamespace.events.show, () => this.tdArrived.hide());
            $('arrivedAtPicker').addEventListener(VanillaPickerNamespace.events.show, () => this.tdDeparting.hide());
            document.addEventListener('click', event => {
                if (!$('departingAtPicker').contains(event.target) && !this.tdDeparting._widget.contains(event.target)) this.tdDeparting.hide();
                if (!$('arrivedAtPicker').contains(event.target) && !this.tdArrived._widget.contains(event.target)) this.tdArrived.hide();
            });
        }

        updatePlaceholder(element) {
            if (element.id === 'interval') return;
            element.classList.toggle('is-placeholder', element.tagName === 'SELECT' ? element.value === '' : element.value === element.defaultValue);
        }

        bindFields() {
            document.querySelectorAll('.form-panel input, .form-panel select').forEach(element => {
                const clear = () => {
                    element.closest('.field-group')?.classList.remove('has-error');
                    this.updatePlaceholder(element);
                };
                element.addEventListener(element.tagName === 'SELECT' ? 'change' : 'input', clear);
                if (element.tagName === 'SELECT') element.addEventListener('input', clear);
            });
            document.querySelectorAll('.form-panel select').forEach(select => this.updatePlaceholder(select));
        }

        inputData() {
            const data = {};
            FORM_FIELDS.forEach(id => { data[id] = $(id).value.trim(); });
            data.interval = parseInt($('interval').value, 10) || 30;
            return data;
        }

        validate(data) {
            document.querySelectorAll('.field-group.has-error').forEach(group => group.classList.remove('has-error'));
            const messages = [];
            FORM_FIELDS.forEach(id => {
                const rule = FORMAT_RULES[id];
                if (!data[id] || (rule && !rule.pattern.test(data[id]))) {
                    $(id).closest('.field-group')?.classList.add('has-error');
                    if (data[id] && rule) messages.push(rule.message);
                }
            });
            const first = document.querySelector('.field-group.has-error');
            if (!first) return true;
            first.scrollIntoView({behavior: 'smooth', block: 'center'});
            setTimeout(() => first.querySelector('input, select')?.focus({preventScroll: true}), 400);
            if (messages.length) messages.forEach(message => UI.toast(message, 'error'));
            else UI.toast(`${document.querySelectorAll('.field-group.has-error').length}개 필수 항목을 확인해주세요`, 'error');
            return false;
        }

        updateUI(running) {
            this.running = running;
            $('btn-start').disabled = running;
            $('btn-stop').disabled = !running;
            UI.statusBadge.set(running ? {label: '스케줄 실행 중', short: '실행 중', tone: 'running'} : {label: '대기 중', short: '대기', tone: 'idle'});
            document.querySelector('.form-panel').classList.toggle('form-panel--active', running);
        }

        schedulePoll() {
            clearTimeout(this.pollTimer);
            this.pollTimer = this.running ? setTimeout(() => this.fetchLogs(), 2000) : null;
        }

        async fetchLogs() {
            // 요청마다 번호를 매겨, 늦게 도착한 옛 응답이 새 상태를 덮지 않게 한다.
            // 폴링은 응답을 받은 뒤에 다음 요청을 예약하는 한 줄짜리 루프다.
            const seq = ++this.fetchSeq;
            clearTimeout(this.pollTimer);
            try {
                const data = await UI.api(API + '/logs');
                if (seq !== this.fetchSeq) return;
                const logs = data.logs || [];
                this.logPanel.render(logs.map(toEntry));
                this.updateUI(data.running);
                this.notifyNewSuccess(logs);
            } catch (_) {
                // 실패해도 실행 중이면 다음 주기에 다시 읽는다.
            } finally {
                if (seq === this.fetchSeq) this.schedulePoll();
            }
        }

        notifyNewSuccess(logs) {
            const successId = latestSuccessId(logs);
            if (!this.initialLogsFetched) {
                // 첫 조회 때는 이미 있던 성공을 기록만 하고 알리지 않는다.
                this.initialLogsFetched = true;
                this.lastSuccessId = successId;
            } else if (successId && successId !== this.lastSuccessId) {
                // 설정 팝업이 열려 있으면 다음 폴링에서 다시 판단한다(김포 updateCompletion과 같은 방식).
                if (UI.settings?.isOpen()) return;
                this.lastSuccessId = successId;
                UI.toast('예약이 완료되었습니다', 'success');
                UI.completion.success({title: '예약이', highlight: '완료되었습니다', subtitle: '공항 사이트에서 예약 내역을 확인해주세요.'});
            }
        }

        async start() {
            const data = this.inputData();
            if (!this.validate(data)) return;
            try {
                await UI.api(API + '/start', {method: 'POST', data});
                this.updateUI(true);
                await this.fetchLogs();
                setTimeout(() => this.logPanel.openSheet(), 600);
            } catch (error) { UI.toast(error.message, 'error'); }
        }

        async stop() {
            try {
                await UI.api(API + '/stop', {method: 'POST'});
                this.updateUI(false);
                await this.fetchLogs();
            } catch (error) { UI.toast(error.message, 'error'); }
        }

        async testCall() {
            const data = this.inputData();
            if (!this.validate(data)) return;
            const button = $('btn-test');
            const label = button.querySelector('.btn-label');
            button.disabled = true;
            button.classList.add('btn--loading');
            label.textContent = '요청 중…';
            try {
                await UI.api(API + '/test', {method: 'POST', data});
                if (!this.savedDefaults) {
                    if (confirm('예약 정보를 저장할까요?')) await this.saveUserData(data);
                } else if (this.scheduleChanged(data) && confirm('변경한 예약 정보를 저장할까요?')) {
                    await this.saveUserData(data);
                }
                await this.fetchLogs();
                setTimeout(() => this.logPanel.openSheet(), 600);
            } catch (error) {
                UI.toast(error.message, 'error');
            } finally {
                button.disabled = false;
                button.classList.remove('btn--loading');
                label.textContent = '1회 예약 요청';
            }
        }

        async clearLogs() {
            try {
                await UI.api(API + '/logs/clear', {method: 'POST'});
                this.fetchSeq++;  // 초기화 전에 보낸 로그 요청의 응답은 버린다
                this.logPanel.render([]);
                this.lastSuccessId = null;
                this.schedulePoll();
            } catch (error) { UI.toast(error.message, 'error'); }
        }

        async loadDefaults() {
            try {
                const data = await UI.api(API + '/defaults');
                FORM_FIELDS.forEach(id => { if (!DATE_FIELDS.includes(id) && data[id]) $(id).value = data[id]; });
                if (data.departingAt) this.tdDeparting.dates.setValue(toPickerDate(data.departingAt));
                if (data.arrivedAt) this.tdArrived.dates.setValue(toPickerDate(data.arrivedAt));
                if (data.interval) $('interval').value = data.interval;
                this.savedDefaults = data.hasSavedData ? data : null;
                ['carBrand', 'carColor', 'departingAir'].forEach(id => this.updatePlaceholder($(id)));
                DATE_FIELDS.forEach(id => { if ($(id).value) $(id).classList.toggle('is-placeholder', !data.hasSavedData); });
            } catch (_) { /* 저장된 정보가 없으면 빈 폼으로 시작한다 */ }
        }

        async saveUserData(data) {
            try {
                await UI.api(API + '/save-defaults', {method: 'POST', data});
                this.savedDefaults = {...data, hasSavedData: true};
                UI.toast('정보가 저장되었습니다', 'success');
            } catch (error) { UI.toast('저장 실패: ' + error.message, 'error'); }
        }

        scheduleChanged(current) {
            if (!this.savedDefaults) return false;
            return ['departingAt', 'arrivedAt', 'departingAir'].some(key => (current[key] || '') !== (this.savedDefaults[key] || ''));
        }
    }

    document.addEventListener('DOMContentLoaded', () => { window.t2Screen = new T2Screen(); });
})();
