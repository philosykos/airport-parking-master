let pollInterval = null;
let currentLogs = [];
let lastSuccessId = null;
let initialLogsFetched = false;
var savedDefaults = null;
const API_BASE = '/t2-valet/api';

// SVG icon constants
const SVG_ZAP = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/></svg>';
const SVG_LOADER = '<svg class="spin-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>';
const SVG_EMPTY = '<svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M12 11h4"/><path d="M12 16h4"/><path d="M8 11h.01"/><path d="M8 16h.01"/></svg>';
const SVG_COPY = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
const SVG_CHECK = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20 6 9 17l-5-5"/></svg>';
const EMPTY_LOG_ROW = '<tr><td colspan="4" class="empty-msg"><div class="empty-icon">' + SVG_EMPTY + '</div>아직 예약 요청 내역이 없습니다</td></tr>';

// 예약 입력 필드. 서버의 services/t2/validation.py FIELDS와 같은 목록·같은 형식 규칙을 쓴다.
const FORM_FIELDS = ['name', 'phone', 'carNumber', 'carModel', 'carBrand', 'carColor', 'departingAt', 'arrivedAt', 'departingAir'];
const DATE_FIELDS = ['departingAt', 'arrivedAt'];
const FORMAT_RULES = {
    phone: { pattern: /^010\d{8}$/, message: '휴대전화 번호는 010으로 시작하는 숫자 11자리로 입력해주세요.' },
    carNumber: { pattern: /^\d{2,3}[가-힣]\d{4}$/, message: '차량번호 형식을 확인해주세요. 예: 12가3456, 123가4567' }
};
const OVERLAY_IDS = ['detail-overlay', 'log-sheet-overlay', 'select-picker-overlay'];

function updatePlaceholderState(el) {
    if (el.id === 'interval') return;
    if (el.tagName === 'SELECT') {
        el.classList.toggle('is-placeholder', el.value === '');
    } else {
        el.classList.toggle('is-placeholder', el.value === el.defaultValue);
    }
}

function showError(msg) {
    const el = document.getElementById('error-msg');
    if (msg) {
        el.textContent = msg;
        el.style.display = 'block';
    } else {
        el.style.display = 'none';
    }
}

// 저장된 "YYYY-MM-DD HH:mm[:ss]" 문자열을 데이트피커가 받는 Date로 바꾼다
function toPickerDate(val) {
    return new Date(val.substring(0, 16).replace(' ', 'T'));
}

function getInputData() {
    var data = {};
    FORM_FIELDS.forEach(function(id) {
        data[id] = document.getElementById(id).value.trim();
    });
    data.interval = parseInt(document.getElementById('interval').value) || 30;
    return data;
}

function clearFieldErrors() {
    document.querySelectorAll('.field-group.has-error').forEach(function(el) {
        el.classList.remove('has-error');
    });
}

function setFieldError(id) {
    var input = document.getElementById(id);
    if (input) {
        var group = input.closest('.field-group');
        if (group) group.classList.add('has-error');
    }
}

function validateInput(data) {
    clearFieldErrors();
    var valid = true;
    var errors = [];

    FORM_FIELDS.forEach(function(id) {
        var rule = FORMAT_RULES[id];
        if (!data[id]) {
            setFieldError(id);
            valid = false;
        } else if (rule && !rule.pattern.test(data[id])) {
            setFieldError(id);
            valid = false;
            errors.push(rule.message);
        }
    });

    if (!valid) {
        var firstError = document.querySelector('.field-group.has-error');
        if (firstError) {
            firstError.scrollIntoView({ behavior: 'smooth', block: 'center' });
            var firstInput = firstError.querySelector('input, select');
            if (firstInput) {
                setTimeout(function() {
                    firstInput.focus({ preventScroll: true });
                }, 400);
            }
        }
        if (errors.length > 0) {
            errors.forEach(function(msg) { showToast(msg, 'error'); });
        } else {
            var errorCount = document.querySelectorAll('.field-group.has-error').length;
            showToast(errorCount + '개 필수 항목을 확인해주세요', 'error');
        }
    }

    return valid;
}

// Clear error on input & update placeholder state
document.querySelectorAll('input, select').forEach(function(el) {
    var eventName = el.tagName === 'SELECT' ? 'change' : 'input';
    el.addEventListener(eventName, function() {
        var group = el.closest('.field-group');
        if (group) group.classList.remove('has-error');
        updatePlaceholderState(el);
    });
    // Also listen to 'input' for selects to catch programmatic changes
    if (el.tagName === 'SELECT') {
        el.addEventListener('input', function() {
            updatePlaceholderState(el);
        });
    }
});

// Button ripple effect
document.addEventListener('click', function(e) {
    var btn = e.target.closest('button');
    if (!btn || btn.disabled) return;
    var rect = btn.getBoundingClientRect();
    var ripple = document.createElement('span');
    ripple.className = 'btn-ripple';
    ripple.style.left = (e.clientX - rect.left) + 'px';
    ripple.style.top = (e.clientY - rect.top) + 'px';
    btn.appendChild(ripple);
    setTimeout(function() { ripple.remove(); }, 600);
});

// POST 요청 공통 처리. body가 있으면 JSON으로 보낸다. 응답은 { ok, data }.
// 서버가 JSON이 아닌 오류 페이지(400·500 HTML)를 돌려주면 data.error에 상태 코드를 담는다.
async function apiPost(path, body) {
    var options = { method: 'POST' };
    if (body !== undefined) {
        options.headers = { 'Content-Type': 'application/json' };
        options.body = JSON.stringify(body);
    }
    var resp = await fetch(API_BASE + path, options);
    var data;
    try {
        data = await resp.json();
    } catch (e) {
        data = { error: '서버 오류 (HTTP ' + resp.status + ')' };
    }
    return { ok: resp.ok, data: data };
}

async function testCall() {
    showError('');
    const data = getInputData();
    if (!validateInput(data)) return;

    const btn = document.getElementById('btn-test');
    btn.disabled = true;
    btn.classList.add('btn--loading');
    btn.innerHTML = SVG_LOADER + ' 요청 중…';

    try {
        const result = await apiPost('/test', data);
        if (!result.ok) {
            showError(result.data.error);
            return;
        }

        if (!savedDefaults) {
            if (confirm('예약 정보를 저장할까요?')) {
                await saveUserData(data);
            }
        } else if (hasTravelScheduleChanged(data)) {
            if (confirm('변경한 예약 정보를 저장할까요?')) {
                await saveUserData(data);
            }
        }

        fetchLogs();
        if (isMobile()) {
            setTimeout(function() { openMobileLog(); }, 600);
        }
    } catch (e) {
        showError('서버 통신 오류: ' + e.message);
    } finally {
        btn.disabled = false;
        btn.classList.remove('btn--loading');
        btn.innerHTML = SVG_ZAP + ' 1회 예약 요청';
    }
}

async function startPolling() {
    showError('');
    const data = getInputData();
    if (!validateInput(data)) return;

    try {
        const result = await apiPost('/start', data);
        if (!result.ok) {
            showError(result.data.error);
            return;
        }
        updateUI(true);
        startLogPolling();
        if (isMobile()) {
            setTimeout(function() { openMobileLog(); }, 600);
        }
    } catch (e) {
        showError('서버 통신 오류: ' + e.message);
    }
}

async function stopPolling() {
    try {
        const result = await apiPost('/stop');
        if (!result.ok) {
            showError(result.data.error);
            return;
        }
        updateUI(false);
        stopLogPolling();
    } catch (e) {
        showError('서버 통신 오류: ' + e.message);
    }
}

function renderStatusBadge(badge, running) {
    if (!badge) return;
    badge.innerHTML = '<span class="status-dot"></span><span class="status-label">' + (running ? '실행 중' : '대기') + '</span>';
    badge.className = running ? 'log-status running' : 'log-status';
}

function updateUI(running) {
    document.getElementById('btn-start').disabled = running;
    document.getElementById('btn-stop').disabled = !running;
    renderStatusBadge(document.getElementById('status-badge'), running);
    renderStatusBadge(document.getElementById('mobile-status-badge'), running);
    document.getElementById('header-dot').className = running ? 'ping-ring active' : 'ping-ring';
    document.getElementById('header-status-text').textContent = running ? '스케줄 실행 중' : '대기 중';
    document.querySelector('.form-panel').classList.toggle('form-panel--active', running);
}

function startLogPolling() {
    if (pollInterval) clearInterval(pollInterval);
    fetchLogs();
    pollInterval = setInterval(fetchLogs, 2000);
}

function stopLogPolling() {
    if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
    }
    fetchLogs();
}

async function fetchLogs() {
    try {
        const resp = await fetch(API_BASE + '/logs');
        const data = await resp.json();
        renderLogs(data.logs);
        updateUI(data.running);
        if (!data.running && pollInterval) {
            stopLogPolling();
        }
        notifyNewSuccess(data.logs || []);
    } catch (e) {}
}

// 마지막 예약 성공(HTTP 200) 로그의 식별자. 스케줄이 성공하면 200 로그 뒤에 SUCCESS 이벤트가
// 붙으므로 마지막 줄만 보면 안 되고 뒤에서부터 찾는다.
function latestSuccessId(logs) {
    for (var i = logs.length - 1; i >= 0; i--) {
        if (logs[i].status === 200) return logs[i].time + '-' + logs[i].type;
    }
    return null;
}

function notifyNewSuccess(logs) {
    var successId = latestSuccessId(logs);
    if (!initialLogsFetched) {
        // 첫 조회 때는 이미 있던 성공을 기록만 하고 알리지 않는다
        initialLogsFetched = true;
        lastSuccessId = successId;
    } else if (successId && successId !== lastSuccessId) {
        lastSuccessId = successId;
        showReservationSuccess();
    }
}

function showReservationSuccess() {
    showToast('예약이 완료되었습니다', 'success');
    document.getElementById('success-overlay').classList.remove('hidden');
}

function typeTag(type) {
    const labels = {
        test: '<span class="tag tag-test">1회 요청</span>',
        schedule: '<span class="tag tag-schedule">자동 예약</span>',
        event: '<span class="tag tag-event">상태</span>',
        notification: '<span class="tag tag-event">알림</span>',
    };
    return labels[type] || '';
}

function escapeHtml(value) {
    return (value == null ? '' : String(value))
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function formatBody(raw) {
    try {
        return JSON.stringify(JSON.parse(raw), null, 2);
    } catch (e) {
        return raw;
    }
}

function syntaxHighlight(json) {
    if (!json) return '';
    var escaped = escapeHtml(json);
    return escaped.replace(
        /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?|\bnull\b)/g,
        function (match) {
            var cls = 'json-number';
            if (/^"/.test(match)) {
                if (/:$/.test(match)) {
                    cls = 'json-key';
                } else {
                    cls = 'json-string';
                }
            } else if (/true|false/.test(match)) {
                cls = 'json-bool';
            } else if (/null/.test(match)) {
                cls = 'json-null';
            }
            return '<span class="' + cls + '">' + match + '</span>';
        }
    );
}

function isOverlayOpen(id) {
    var el = document.getElementById(id);
    return !!el && el.classList.contains('open');
}

// 열린 오버레이가 하나도 없을 때만 본문 스크롤을 되돌린다
function releaseBodyScroll() {
    if (!OVERLAY_IDS.some(isOverlayOpen)) document.body.style.overflow = '';
}

// Focus trap for detail panel
var detailTrapCleanup = null;
var detailTrigger = null;

function trapFocus(element) {
    var focusableSelector = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';
    var focusables = element.querySelectorAll(focusableSelector);
    if (focusables.length === 0) return function() {};
    var first = focusables[0];
    var last = focusables[focusables.length - 1];
    function handler(e) {
        if (e.key !== 'Tab') return;
        if (e.shiftKey) {
            if (document.activeElement === first) { e.preventDefault(); last.focus(); }
        } else {
            if (document.activeElement === last) { e.preventDefault(); first.focus(); }
        }
    }
    element.addEventListener('keydown', handler);
    return function() { element.removeEventListener('keydown', handler); };
}

function addCopyButton(block, textToCopy) {
    var copyBtn = document.createElement('button');
    copyBtn.className = 'detail-copy-btn';
    copyBtn.innerHTML = SVG_COPY + ' Copy';
    copyBtn.onclick = function(e) {
        e.stopPropagation();
        navigator.clipboard.writeText(textToCopy).then(function() {
            copyBtn.innerHTML = SVG_CHECK + ' Copied!';
            copyBtn.classList.add('copied');
            setTimeout(function() {
                copyBtn.innerHTML = SVG_COPY + ' Copy';
                copyBtn.classList.remove('copied');
            }, 2000);
        });
    };
    block.appendChild(copyBtn);
}

function statusBadge(status) {
    var cls = 'detail-status';
    var code = parseInt(status);
    if (code >= 200 && code < 300) cls += ' status-success';
    else if (code >= 400 && code < 500) cls += ' status-warning';
    else if (code >= 500) cls += ' status-error';
    return '<span class="' + cls + '">' + (status ? escapeHtml(status) : '\u2014') + '</span>';
}

function openDetail(logEntry) {
    var overlay = document.getElementById('detail-overlay');
    var meta = document.getElementById('detail-meta');
    var json = document.getElementById('detail-json');

    meta.innerHTML = escapeHtml(logEntry.time) + ' <span class="detail-meta-sep">|</span> ' + typeTag(logEntry.type) + ' <span class="detail-meta-sep">|</span> ' + statusBadge(logEntry.status);

    var method = logEntry.method || 'POST';
    var sections = '';
    if (logEntry.url) {
        sections += '<div class="detail-section"><div class="detail-section-title">REQUEST URL</div>'
            + '<div class="detail-url-block">'
            + '<span class="detail-method-badge" data-method="' + escapeHtml(method) + '">' + escapeHtml(method) + '</span>'
            + '<span class="detail-url-text">' + escapeHtml(logEntry.url) + '</span>'
            + '</div></div>';
    }
    if (logEntry.payload) {
        var payloadFormatted = JSON.stringify(logEntry.payload, null, 2);
        sections += '<div class="detail-section"><div class="detail-section-title">REQUEST PAYLOAD</div><div class="detail-json" id="detail-json-payload">' + syntaxHighlight(payloadFormatted) + '</div></div>';
    }
    var formatted = formatBody(logEntry.body || '');
    sections += '<div class="detail-section"><div class="detail-section-title">RESPONSE BODY</div><div class="detail-json" id="detail-json-response">' + syntaxHighlight(formatted) + '</div></div>';
    json.innerHTML = sections;

    // Add copy buttons to JSON blocks
    var payloadBlock = document.getElementById('detail-json-payload');
    if (payloadBlock) {
        addCopyButton(payloadBlock, payloadFormatted);
    }
    var responseBlock = document.getElementById('detail-json-response');
    if (responseBlock) {
        addCopyButton(responseBlock, formatted);
    }

    overlay.classList.add('open');
    document.body.style.overflow = 'hidden';

    detailTrigger = document.activeElement;
    detailTrapCleanup = trapFocus(document.querySelector('.detail-panel'));
}

function closeDetail(event) {
    if (event && event.target !== event.currentTarget) return;
    if (detailTrapCleanup) { detailTrapCleanup(); detailTrapCleanup = null; }
    document.getElementById('detail-overlay').classList.remove('open');
    if (detailTrigger) { detailTrigger.focus(); detailTrigger = null; }
    releaseBodyScroll();
}

// Escape: 맨 위에 떠 있는 것부터 닫는다
document.addEventListener('keydown', function(e) {
    if (e.key !== 'Escape') return;
    if (isOverlayOpen('select-picker-overlay')) {
        closeSelectPicker();
        e.stopImmediatePropagation();
        return;
    }
    document.getElementById('success-overlay').classList.add('hidden');
    if (isOverlayOpen('log-sheet-overlay')) {
        closeMobileLog();
    } else if (isOverlayOpen('detail-overlay')) {
        closeDetail();
    }
}, true);  // 캡처 단계: 선택 시트 Escape를 다른 처리기보다 먼저 가로챈다

/* ── Mobile Log Bottom Sheet ── */
function isMobile() {
    return window.matchMedia('(max-width: 960px)').matches;
}

function toggleMobileLog() {
    if (isOverlayOpen('log-sheet-overlay')) {
        closeMobileLog();
    } else {
        openMobileLog();
    }
}

function openMobileLog() {
    var overlay = document.getElementById('log-sheet-overlay');
    overlay.classList.add('open');
    document.body.style.overflow = 'hidden';
    syncMobileLog();
}

function closeMobileLog(event) {
    if (event && event.target !== event.currentTarget) return;
    document.getElementById('log-sheet-overlay').classList.remove('open');
    releaseBodyScroll();
}

function syncMobileLog() {
    var mobileBody = document.getElementById('mobile-log-body');
    var tableContainer = document.querySelector('.log-table-container');
    if (mobileBody && tableContainer) {
        mobileBody.innerHTML = tableContainer.outerHTML;
        mobileBody.querySelectorAll('tr.log-row').forEach(function(row) {
            row.onclick = function() {
                openDetail(currentLogs[Number(row.dataset.logIndex)]);
            };
        });
    }
}

// 바텀시트 손잡이를 아래로 100px 넘게 끌면 닫는다
function enableSwipeDismiss(sheetId, handleSelector, close) {
    var sheet = document.getElementById(sheetId);
    var handle = sheet && sheet.querySelector(handleSelector);
    if (!handle) return;
    var startY = 0, currentY = 0, isDragging = false;

    handle.addEventListener('touchstart', function(e) {
        startY = e.touches[0].clientY;
        isDragging = true;
        sheet.style.transition = 'none';
    });

    document.addEventListener('touchmove', function(e) {
        if (!isDragging) return;
        currentY = e.touches[0].clientY - startY;
        if (currentY > 0) {
            sheet.style.transform = 'translateY(' + currentY + 'px)';
        }
    });

    document.addEventListener('touchend', function() {
        if (!isDragging) return;
        isDragging = false;
        sheet.style.transition = '';
        if (currentY > 100) close();
        sheet.style.transform = '';
        currentY = 0;
    });
}

enableSwipeDismiss('log-sheet', '.log-sheet-handle', function() { closeMobileLog(); });
enableSwipeDismiss('select-picker', '.select-picker-handle', function() { closeSelectPicker(); });

/* ── Mobile Select Picker ── */
var activeSelect = null;

function openSelectPicker(selectEl) {
    activeSelect = selectEl;
    var overlay = document.getElementById('select-picker-overlay');
    var title = document.getElementById('select-picker-title');
    var body = document.getElementById('select-picker-body');

    // Get label text for title
    var group = selectEl.closest('.field-group');
    var label = group ? group.querySelector('label') : null;
    title.textContent = label ? label.textContent : '선택';

    // Build option items
    var html = '';
    Array.from(selectEl.options).forEach(function(opt, i) {
        if (i === 0 && !opt.value) return; // skip placeholder "선택"
        var isSelected = opt.value === selectEl.value;
        html += '<button class="select-picker-item' + (isSelected ? ' selected' : '') +
            '" data-value="' + escapeHtml(opt.value) + '">' +
            escapeHtml(opt.textContent) + '</button>';
    });
    body.innerHTML = html;

    overlay.classList.add('open');
    document.body.style.overflow = 'hidden';

    // Scroll to selected item
    requestAnimationFrame(function() {
        var selected = body.querySelector('.selected');
        if (selected) {
            selected.scrollIntoView({ block: 'center' });
        }
    });
}

function selectPickerItem(itemEl) {
    if (!activeSelect) return;
    var value = itemEl.getAttribute('data-value');
    activeSelect.value = value;
    // Trigger change event
    activeSelect.dispatchEvent(new Event('change', { bubbles: true }));
    updatePlaceholderState(activeSelect);
    closeSelectPicker();
}

function closeSelectPicker(event) {
    if (event && event.target !== event.currentTarget) return;
    document.getElementById('select-picker-overlay').classList.remove('open');
    activeSelect = null;
    releaseBodyScroll();
}

// Intercept select taps on mobile
document.querySelectorAll('.form-panel select').forEach(function(sel) {
    sel.addEventListener('mousedown', function(e) {
        if (isMobile()) {
            e.preventDefault();
            openSelectPicker(sel);
        }
    });
    sel.addEventListener('touchend', function(e) {
        if (isMobile()) {
            e.preventDefault();
            openSelectPicker(sel);
        }
    });
});

function renderLogs(logs) {
    var tbody = document.getElementById('log-body');
    var countEl = document.getElementById('log-count');
    currentLogs = logs || [];

    if (!logs || logs.length === 0) {
        tbody.innerHTML = EMPTY_LOG_ROW;
        countEl.textContent = '0';
        updateMobileLogBadge(0);
        return;
    }

    countEl.textContent = logs.length;
    countEl.classList.remove('bounce');
    void countEl.offsetWidth; // force reflow
    countEl.classList.add('bounce');

    tbody.innerHTML = logs.slice().reverse().map(function(log, i) {
        var isEvent = log.type === 'event';
        var isOk = typeof log.status === 'number' && log.status >= 200 && log.status < 300;
        var statusClass = isEvent ? '' : (typeof log.status === 'number' ? (isOk ? 'status-ok' : 'status-err') : 'status-err');
        var rowClass = isEvent ? 'row-event log-row' : 'log-row';
        var body = log.body == null ? '' : String(log.body);
        var bodyShort = escapeHtml(body.substring(0, 80));
        var delay = Math.min(i * 50, 250);
        var logIdx = logs.length - 1 - i;
        return '<tr class="' + rowClass + '" style="animation-delay:' + delay + 'ms" data-log-index="' + logIdx + '">' +
            '<td class="cell-time">' + escapeHtml(log.time) + '</td>' +
            '<td>' + typeTag(log.type) + '</td>' +
            '<td><span class="cell-status ' + statusClass + '">' + escapeHtml(log.status) + '</span></td>' +
            '<td class="body-cell">' + bodyShort + (body.length > 80 ? '\u2026' : '') + '</td>' +
        '</tr>';
    }).join('');

    // Sync mobile log
    updateMobileLogBadge(logs.length);
    var mobileCount = document.getElementById('mobile-log-count');
    if (mobileCount) mobileCount.textContent = logs.length;
    var logSheet = document.getElementById('log-sheet-overlay');
    if (logSheet && logSheet.classList.contains('open')) {
        syncMobileLog();
    }
}

function updateMobileLogBadge(count) {
    var badge = document.getElementById('log-fab-badge');
    if (badge) {
        badge.textContent = count > 0 ? count : '';
    }
}

function showToast(message, type) {
    var container = document.getElementById('toast-container');

    while (container.children.length >= 3) {
        container.removeChild(container.firstChild);
    }

    var toast = document.createElement('div');
    toast.className = 'toast toast-' + type;

    var icon = type === 'success'
        ? '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M20 6 9 17l-5-5"/></svg>'
        : '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/></svg>';

    toast.innerHTML = icon + ' ' + escapeHtml(message);

    var progress = document.createElement('div');
    progress.className = 'toast-progress';
    toast.appendChild(progress);

    container.appendChild(toast);

    requestAnimationFrame(function() {
        requestAnimationFrame(function() {
            toast.classList.add('show');
        });
    });

    var remaining = 3000;
    var start = Date.now();
    var timer;
    function startTimer() {
        start = Date.now();
        timer = setTimeout(dismiss, remaining);
    }
    function dismiss() {
        toast.classList.remove('show');
        toast.classList.add('hiding');
        setTimeout(function() { toast.remove(); }, 400);
    }
    toast.addEventListener('mouseenter', function() {
        clearTimeout(timer);
        remaining -= (Date.now() - start);
        progress.style.animationPlayState = 'paused';
    });
    toast.addEventListener('mouseleave', function() {
        progress.style.animationPlayState = 'running';
        startTimer();
    });
    startTimer();
}

async function clearLogs() {
    try {
        var result = await apiPost('/logs/clear');
        if (!result.ok) return;
        renderLogs([]);
        lastSuccessId = null;
        var mobileCount = document.getElementById('mobile-log-count');
        if (mobileCount) mobileCount.textContent = '0';
        var mobileBody = document.getElementById('mobile-log-body');
        if (mobileBody) mobileBody.innerHTML = '';
    } catch (e) {}
}

async function loadDefaults() {
    try {
        const resp = await fetch(API_BASE + '/defaults');
        const data = await resp.json();
        FORM_FIELDS.forEach(function(id) {
            if (DATE_FIELDS.indexOf(id) === -1 && data[id]) document.getElementById(id).value = data[id];
        });
        if (data.departingAt) tdDeparting.dates.setValue(toPickerDate(data.departingAt));
        if (data.arrivedAt) tdArrived.dates.setValue(toPickerDate(data.arrivedAt));
        if (data.interval) document.getElementById('interval').value = data.interval;

        savedDefaults = data.hasSavedData ? data : null;

        // 저장된 데이터가 아닌 경우에만 가이드 텍스트(placeholder) 상태 유지
        ['carBrand', 'carColor', 'departingAir'].forEach(function(id) {
            updatePlaceholderState(document.getElementById(id));
        });
        DATE_FIELDS.forEach(function(id) {
            var el = document.getElementById(id);
            if (el.value) {
                if (data.hasSavedData) {
                    el.classList.remove('is-placeholder');
                } else {
                    el.classList.add('is-placeholder');
                }
            }
        });
    } catch (e) {}
}

async function saveUserData(data) {
    try {
        var result = await apiPost('/save-defaults', data);
        if (result.ok) {
            savedDefaults = Object.assign({}, data, { hasSavedData: true });
            showToast('정보가 저장되었습니다', 'success');
        } else {
            showToast('저장 실패: ' + result.data.error, 'error');
        }
    } catch (e) {
        showToast('저장 실패: ' + e.message, 'error');
    }
}

function hasTravelScheduleChanged(current) {
    if (!savedDefaults) return false;
    var fields = ['departingAt', 'arrivedAt', 'departingAir'];
    return fields.some(function(f) { return (current[f] || '') !== (savedDefaults[f] || ''); });
}

// Scroll shadow on table header
(function() {
    var logScroll = document.querySelector('.log-scroll');
    var tableContainer = document.querySelector('.log-table-container');
    if (logScroll && tableContainer) {
        logScroll.addEventListener('scroll', function() {
            tableContainer.classList.toggle('scrolled', logScroll.scrollTop > 0);
        });
    }
})();

// VanillaPicker 초기화
var now = new Date();
var depDefault = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 9, 0);
var arrDefault = new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000);
arrDefault = new Date(arrDefault.getFullYear(), arrDefault.getMonth(), arrDefault.getDate(), 18, 0);

var tdDeparting = new VanillaPicker(
    document.getElementById('departingAtPicker'),
    { defaultDate: depDefault, format: 'yyyy-MM-dd HH:mm', locale: 'ko', stepping: 5 }
);
document.getElementById('departingAt').classList.add('is-placeholder');

var tdArrived = new VanillaPicker(
    document.getElementById('arrivedAtPicker'),
    { defaultDate: arrDefault, format: 'yyyy-MM-dd HH:mm', locale: 'ko', stepping: 5 }
);
document.getElementById('arrivedAt').classList.add('is-placeholder');

// 날짜 범위 초기 설정 및 변경 시 동기화 (양쪽 picker 모두 start/end 전달)
tdDeparting.setRange(tdDeparting.dates.lastPicked, tdArrived.dates.lastPicked);
tdArrived.setRange(tdDeparting.dates.lastPicked, tdArrived.dates.lastPicked);
tdArrived.setMinDate(tdDeparting.dates.lastPicked);

document.getElementById('departingAtPicker').addEventListener('vp.change', function() {
    // 출발일시 변경 시 도착일시 초기화
    var depDate = tdDeparting.dates.lastPicked;
    var resetArr = new Date(depDate);
    resetArr.setHours(18, 0, 0, 0);
    tdArrived.setMinDate(depDate);
    tdArrived.dates.setValue(resetArr);
    document.getElementById('arrivedAt').classList.add('is-placeholder');
    tdDeparting.setRange(depDate, resetArr);
    tdArrived.setRange(depDate, resetArr);
});
document.getElementById('arrivedAtPicker').addEventListener('vp.change', function() {
    tdDeparting.setRange(tdDeparting.dates.lastPicked, tdArrived.dates.lastPicked);
    tdArrived.setRange(tdDeparting.dates.lastPicked, tdArrived.dates.lastPicked);
});

// Change 이벤트 구독
['departingAtPicker', 'arrivedAtPicker'].forEach(function(pickerId) {
    var inputId = pickerId === 'departingAtPicker' ? 'departingAt' : 'arrivedAt';
    document.getElementById(pickerId).addEventListener(
        VanillaPickerNamespace.events.change,
        function(e) {
            var el = document.getElementById(inputId);
            var group = el.closest('.field-group');
            if (group) group.classList.remove('has-error');
            el.classList.toggle('is-placeholder', !el.value);
        }
    );
});

// ── iOS 스타일 스크롤 시간 피커 ──
function createScrollTimePicker(tdInstance, pickerElId) {
    var ITEM_HEIGHT = 36;
    var VISIBLE_ITEMS = 5;
    var MINUTE_STEP = 5;

    function buildColumn(values, selectedValue) {
        var col = document.createElement('div');
        col.className = 'td-scroll-column';
        var inner = document.createElement('div');
        inner.className = 'td-scroll-column-inner';
        // Top/bottom padding so first/last items can center
        var pad = Math.floor(VISIBLE_ITEMS / 2);
        for (var p = 0; p < pad; p++) {
            var spacer = document.createElement('div');
            spacer.className = 'td-scroll-item td-scroll-spacer';
            inner.appendChild(spacer);
        }
        values.forEach(function(val, idx) {
            var item = document.createElement('div');
            item.className = 'td-scroll-item';
            item.textContent = String(val).padStart(2, '0');
            item.dataset.value = val;
            item.addEventListener('click', function() {
                inner.scrollTo({ top: idx * ITEM_HEIGHT, behavior: 'smooth' });
            });
            inner.appendChild(item);
        });
        for (var p2 = 0; p2 < pad; p2++) {
            var spacer2 = document.createElement('div');
            spacer2.className = 'td-scroll-item td-scroll-spacer';
            inner.appendChild(spacer2);
        }
        col.appendChild(inner);
        // Highlight band
        var highlight = document.createElement('div');
        highlight.className = 'td-scroll-highlight';
        col.appendChild(highlight);
        // Fade overlays
        var fadeTop = document.createElement('div');
        fadeTop.className = 'td-scroll-fade td-scroll-fade-top';
        col.appendChild(fadeTop);
        var fadeBottom = document.createElement('div');
        fadeBottom.className = 'td-scroll-fade td-scroll-fade-bottom';
        col.appendChild(fadeBottom);
        return { el: col, inner: inner, values: values };
    }

    function scrollToValue(col, value) {
        var idx = col.values.indexOf(value);
        if (idx === -1) idx = 0;
        col.inner.scrollTop = idx * ITEM_HEIGHT;
    }

    function getSelectedValue(col) {
        var scrollTop = col.inner.scrollTop;
        var idx = Math.round(scrollTop / ITEM_HEIGHT);
        idx = Math.max(0, Math.min(idx, col.values.length - 1));
        return col.values[idx];
    }

    function updateActiveState(col) {
        var idx = Math.round(col.inner.scrollTop / ITEM_HEIGHT);
        var items = col.inner.querySelectorAll('.td-scroll-item:not(.td-scroll-spacer)');
        items.forEach(function(item, i) {
            item.classList.toggle('active', i === idx);
        });
    }

    var hours = [];
    for (var h = 0; h < 24; h++) hours.push(h);
    var minutes = [];
    for (var m = 0; m < 60; m += MINUTE_STEP) minutes.push(m);

    var hourCol = buildColumn(hours, 0);
    var minuteCol = buildColumn(minutes, 0);

    var container = document.createElement('div');
    container.className = 'td-scroll-time';
    var sep = document.createElement('div');
    sep.className = 'td-scroll-sep';
    sep.textContent = ':';
    container.appendChild(hourCol.el);
    container.appendChild(sep);
    container.appendChild(minuteCol.el);

    var syncing = false;

    function syncToTD() {
        if (syncing) return;
        syncing = true;
        var hr = getSelectedValue(hourCol);
        var mn = getSelectedValue(minuteCol);
        var current = tdInstance.dates.lastPicked;
        if (current) {
            var d = new Date(current);
            d.setHours(hr);
            d.setMinutes(mn);
            tdInstance.dates.setValue(d);
        }
        syncing = false;
    }

    function onScroll(col) {
        updateActiveState(col);
    }

    var scrollTimer = { hour: null, minute: null };
    hourCol.inner.addEventListener('scroll', function() {
        onScroll(hourCol);
        clearTimeout(scrollTimer.hour);
        scrollTimer.hour = setTimeout(function() { syncToTD(); }, 100);
    });
    minuteCol.inner.addEventListener('scroll', function() {
        onScroll(minuteCol);
        clearTimeout(scrollTimer.minute);
        scrollTimer.minute = setTimeout(function() { syncToTD(); }, 100);
    });

    // Inject into TD widget on show
    var pickerEl = document.getElementById(pickerElId);
    pickerEl.addEventListener(VanillaPickerNamespace.events.show, function() {
        setTimeout(function() {
            var widget = document.querySelector('.tempus-dominus-widget.show');
            if (!widget) return;
            // Hide TD's built-in time container
            var timeContainer = widget.querySelector('.time-container');
            if (timeContainer) timeContainer.style.display = 'none';
            // Append custom scroll picker if not already there
            if (!widget.querySelector('.td-scroll-time')) {
                widget.classList.add('has-scroll-time');
                widget.appendChild(container);
            }
            // Sync scroll position from current date
            var current = tdInstance.dates.lastPicked;
            if (current) {
                var d = new Date(current);
                scrollToValue(hourCol, d.getHours());
                scrollToValue(minuteCol, Math.floor(d.getMinutes() / MINUTE_STEP) * MINUTE_STEP);
            }
            setTimeout(function() {
                updateActiveState(hourCol);
                updateActiveState(minuteCol);
            }, 50);
        }, 10);
    });

}

// 각 피커에 스크롤 시간 피커 연결
createScrollTimePicker(tdDeparting, 'departingAtPicker');
createScrollTimePicker(tdArrived, 'arrivedAtPicker');

// ── 데이트피커 토글 & 외부 클릭 닫기 ──
(function() {
    function setupPickerToggle(pickerElId, otherTd) {
        var pickerEl = document.getElementById(pickerElId);

        // 한 쪽 피커가 열리면 다른 쪽 닫기
        pickerEl.addEventListener(VanillaPickerNamespace.events.show, function() {
            otherTd.hide();
        });
    }

    setupPickerToggle('departingAtPicker', tdArrived);
    setupPickerToggle('arrivedAtPicker', tdDeparting);

    // 외부 클릭 시 닫기
    document.addEventListener('click', function(e) {
        var depEl = document.getElementById('departingAtPicker');
        var arrEl = document.getElementById('arrivedAtPicker');
        if (!depEl.contains(e.target) && !tdDeparting._widget.contains(e.target)) tdDeparting.hide();
        if (!arrEl.contains(e.target) && !tdArrived._widget.contains(e.target)) tdArrived.hide();
    });
})();

// Select 초기 placeholder 상태 설정
document.querySelectorAll('select').forEach(function(el) {
    updatePlaceholderState(el);
});

/* ── 이벤트 연결: CSP(script-src 'self')가 인라인 onclick을 막으므로 여기서 건다 ── */
function onClick(selector, handler) {
    var el = document.querySelector(selector);
    if (el) el.addEventListener('click', handler);
}

onClick('#btn-start', function() { startPolling(); });
onClick('#btn-stop', function() { stopPolling(); });
onClick('#btn-test', function() { testCall(); });
onClick('.btn-clear', function() { clearLogs(); });
onClick('#log-fab', function() { toggleMobileLog(); });
onClick('.success-close-btn', function() {
    document.getElementById('success-overlay').classList.add('hidden');
});
// 배경 클릭만 닫는다: close 함수는 event.target === currentTarget일 때만 닫는다
onClick('#detail-overlay', closeDetail);
onClick('#log-sheet-overlay', closeMobileLog);
onClick('#select-picker-overlay', closeSelectPicker);
onClick('.detail-panel', function(e) { e.stopPropagation(); });
// 닫기 버튼에는 이벤트를 넘기지 않는다. 넘기면 버튼 안 SVG를 누를 때 target이 달라 닫히지 않는다
onClick('.detail-close', function() { closeDetail(); });
onClick('.log-sheet-close', function() { closeMobileLog(); });
onClick('.select-picker-close', function() { closeSelectPicker(); });
document.getElementById('log-body').addEventListener('click', function(e) {
    var row = e.target.closest('tr.log-row');
    if (row) openDetail(currentLogs[Number(row.dataset.logIndex)]);
});
document.getElementById('select-picker-body').addEventListener('click', function(e) {
    var item = e.target.closest('.select-picker-item');
    if (item) selectPickerItem(item);
});

loadDefaults();
fetchLogs();
