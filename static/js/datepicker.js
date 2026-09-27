(function(global) {
    'use strict';

    // Calendar values can represent server wall time, independent of the PC time zone/DST.
    class WallClockDate extends Date {
        constructor(...args) {
            super(args.length > 1 ? Date.UTC(...args) : (args.length ? args[0] : Date.now()));
        }
        toLocaleDateString(locale, options) {
            return super.toLocaleDateString(locale, {...options, timeZone: 'UTC'});
        }
    }
    for (const name of ['FullYear', 'Month', 'Date', 'Day', 'Hours', 'Minutes', 'Seconds', 'Milliseconds']) {
        WallClockDate.prototype['get' + name] = function() { return this['getUTC' + name](); };
        if (name !== 'Day') WallClockDate.prototype['set' + name] = function(...args) { return this['setUTC' + name](...args); };
    }

    // ── Helpers ──────────────────────────────────────────────────────────
    function pad(n) { return String(n).padStart(2, '0'); }

    function formatDate(d) {
        return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate())
               + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
    }

    function isSameDay(a, b) {
        return a && b &&
            a.getFullYear() === b.getFullYear() &&
            a.getMonth()    === b.getMonth()    &&
            a.getDate()     === b.getDate();
    }

    function daysInMonth(year, month) {
        return new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
    }

    // ── Namespace (replaces tempusDominus.Namespace.events) ──────────────
    var VanillaPickerNamespace = {
        events: {
            change: 'vp.change',
            show:   'vp.show',
            hide:   'vp.hide'
        }
    };

    // ── VanillaPicker ─────────────────────────────────────────────────────
    function VanillaPicker(containerEl, options) {
        this._containerEl  = containerEl;
        this._options      = options || {};
        this._Date         = this._options.wallClock ? WallClockDate : Date;
        this._currentDate  = this._options.defaultDate
            ? new this._Date(this._options.defaultDate)
            : new this._Date();
        this._viewDate     = new this._Date(this._currentDate);
        this._isOpen       = false;
        this._rangeStart   = null;
        this._rangeEnd     = null;
        this._minDate      = this._options.minDate ? new this._Date(this._options.minDate) : null;
        this._maxDate      = this._options.maxDate ? new this._Date(this._options.maxDate) : null;
        this._widget       = null;
        this._calGrid      = null;
        this._switchBtn    = null;
        this._input        = containerEl.querySelector('input');

        this._buildWidget();
        this._attachToggleListeners();

        var self = this;
        this.dates = {
            setValue: function(date) { self._setValue(date); },
            get lastPicked() { return self._currentDate; }
        };

        // Set initial input value (caller adds is-placeholder after construction)
        this._updateInput();
    }

    // ── Build widget DOM ─────────────────────────────────────────────────
    VanillaPicker.prototype._buildWidget = function() {
        var widget = document.createElement('div');
        widget.className = 'tempus-dominus-widget';

        var dateContainer = document.createElement('div');
        dateContainer.className = 'date-container-days';

        // Header
        var header = document.createElement('div');
        header.className = 'picker-header';

        var prevBtn = document.createElement('div');
        prevBtn.className = 'previous';
        var prevIcon = document.createElement('span');
        prevIcon.className = 'td-icon-prev';
        prevBtn.appendChild(prevIcon);

        var switchBtn = document.createElement('button');
        switchBtn.type = 'button';
        switchBtn.className = 'picker-switch';
        this._switchBtn = switchBtn;

        var nextBtn = document.createElement('div');
        nextBtn.className = 'next';
        var nextIcon = document.createElement('span');
        nextIcon.className = 'td-icon-next';
        nextBtn.appendChild(nextIcon);

        header.appendChild(prevBtn);
        header.appendChild(switchBtn);
        header.appendChild(nextBtn);

        // Calendar grid
        var calGrid = document.createElement('div');
        calGrid.className = 'calendar-grid';
        this._calGrid = calGrid;

        dateContainer.appendChild(header);
        dateContainer.appendChild(calGrid);
        widget.appendChild(dateContainer);

        // Event: prev/next month
        var self = this;
        prevBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            self._viewDate.setDate(1);
            self._viewDate.setMonth(self._viewDate.getMonth() - 1);
            self._renderCalendar();
        });
        nextBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            self._viewDate.setDate(1);
            self._viewDate.setMonth(self._viewDate.getMonth() + 1);
            self._renderCalendar();
        });

        // Event: day click (delegated)
        calGrid.addEventListener('click', function(e) {
            var target = e.target.closest('.day:not(.disabled)');
            if (!target || target.classList.contains('dow')) return;
            e.stopPropagation();
            var year  = parseInt(target.dataset.year);
            var month = parseInt(target.dataset.month);
            var day   = parseInt(target.dataset.day);
            var newDate = new self._Date(self._currentDate);
            newDate.setFullYear(year, month, day);
            self._setValue(newDate);
        });

        this._widget = widget;
        document.body.appendChild(widget);
        this._renderCalendar();
    };

    // ── Render calendar grid ─────────────────────────────────────────────
    VanillaPicker.prototype._renderCalendar = function() {
        var grid    = this._calGrid;
        var year    = this._viewDate.getFullYear();
        var month   = this._viewDate.getMonth();
        var today   = this._options.now ? this._options.now() : new this._Date();
        var selected = this._currentDate;

        // Update header text
        var headerDate = new this._Date(year, month, 1);
        this._switchBtn.textContent = headerDate.toLocaleDateString('ko-KR', {
            year: 'numeric', month: 'long'
        });

        // Clear existing cells except DOW headers
        while (grid.firstChild) grid.removeChild(grid.firstChild);

        // Weekday headers (일~토)
        var days = ['일', '월', '화', '수', '목', '금', '토'];
        days.forEach(function(d) {
            var span = document.createElement('span');
            span.className = 'dow';
            span.textContent = d;
            grid.appendChild(span);
        });

        // First weekday of this month (0=Sun)
        var firstDay = new this._Date(year, month, 1).getDay();
        // Days in previous month
        var prevMonthDays = daysInMonth(year, month - 1);

        // Leading cells from previous month
        for (var i = 0; i < firstDay; i++) {
            var dayNum = prevMonthDays - firstDay + 1 + i;
            var prevMonth = month - 1;
            var prevYear  = year;
            if (prevMonth < 0) { prevMonth = 11; prevYear--; }
            grid.appendChild(this._makeDay(prevYear, prevMonth, dayNum, 'old'));
        }

        // Current month cells
        var totalDays = daysInMonth(year, month);
        var rangeStart = this._rangeStart;
        var rangeEnd   = this._rangeEnd;
        var todayStart = new this._Date(today.getFullYear(), today.getMonth(), today.getDate());
        var maxDate = this._maxDate || new this._Date(today.getFullYear(), today.getMonth() + 2, today.getDate() - 1);
        var minDate = this._minDate
            ? new this._Date(this._minDate.getFullYear(), this._minDate.getMonth(), this._minDate.getDate())
            : todayStart;
        for (var d = 1; d <= totalDays; d++) {
            var classes = '';
            var cellDate = new this._Date(year, month, d);
            if (cellDate < minDate || cellDate > maxDate) classes += ' disabled';
            if (isSameDay(cellDate, today))    classes += ' today';
            if (isSameDay(cellDate, selected)) classes += ' active';
            if (rangeStart && isSameDay(cellDate, rangeStart)) classes += ' range-start';
            if (rangeEnd   && isSameDay(cellDate, rangeEnd))   classes += ' range-end';
            if (rangeStart && rangeEnd) {
                var ts = cellDate.getTime();
                var rs = new this._Date(rangeStart.getFullYear(), rangeStart.getMonth(), rangeStart.getDate()).getTime();
                var re = new this._Date(rangeEnd.getFullYear(),   rangeEnd.getMonth(),   rangeEnd.getDate()).getTime();
                if (ts > rs && ts < re) classes += ' in-range';
            }
            grid.appendChild(this._makeDay(year, month, d, classes.trim()));
        }

        // Trailing cells from next month
        var totalCells = firstDay + totalDays;
        var remaining  = totalCells % 7 === 0 ? 0 : 7 - (totalCells % 7);
        var nextMonth  = month + 1;
        var nextYear   = year;
        if (nextMonth > 11) { nextMonth = 0; nextYear++; }
        for (var n = 1; n <= remaining; n++) {
            grid.appendChild(this._makeDay(nextYear, nextMonth, n, 'new'));
        }
    };

    VanillaPicker.prototype._makeDay = function(year, month, day, extraClass) {
        var el = document.createElement('div');
        el.className = 'day' + (extraClass ? ' ' + extraClass : '');
        var today = this._options.now ? this._options.now() : new this._Date();
        var min = this._minDate || today;
        var max = this._maxDate || new this._Date(today.getFullYear(), today.getMonth() + 2, today.getDate() - 1);
        var cell = new this._Date(year, month, day);
        if (cell < new this._Date(min.getFullYear(), min.getMonth(), min.getDate()) ||
            cell > new this._Date(max.getFullYear(), max.getMonth(), max.getDate())) el.classList.add('disabled');
        el.textContent = day;
        el.dataset.year  = year;
        el.dataset.month = month;
        el.dataset.day   = day;
        return el;
    };

    // ── setValue ──────────────────────────────────────────────────────────
    VanillaPicker.prototype._setValue = function(date) {
        var d = new this._Date(date);
        if (this._options.validate && !this._options.validate(d)) {
            this._input.setCustomValidity('예약 가능한 날짜와 10분 단위 시간을 확인해주세요.');
            return;
        }
        this._input.setCustomValidity('');
        this._currentDate = d;
        this._viewDate    = new this._Date(d);
        this._updateInput();
        if (this._isOpen) this._renderCalendar();
        this._dispatch(VanillaPickerNamespace.events.change);
    };

    VanillaPicker.prototype._updateInput = function() {
        if (this._input) {
            this._input.value = formatDate(this._currentDate);
        }
    };

    // ── Show / Hide ───────────────────────────────────────────────────────
    VanillaPicker.prototype.show = function() {
        this._widget.classList.add('show');
        this._isOpen = true;
        this._renderCalendar();
        this._position();
        this._dispatch(VanillaPickerNamespace.events.show);
    };

    VanillaPicker.prototype.hide = function() {
        this._widget.classList.remove('show');
        this._isOpen = false;
        this._dispatch(VanillaPickerNamespace.events.hide);
    };

    // ── Positioning ───────────────────────────────────────────────────────
    VanillaPicker.prototype._position = function() {
        var rect    = this._containerEl.getBoundingClientRect();
        var widget  = this._widget;
        var widgetH = widget.offsetHeight;
        var widgetW = widget.offsetWidth;
        var top     = rect.bottom + 6;
        var left    = rect.left;

        // Flip up if overflows viewport bottom
        if (top + widgetH > window.innerHeight) {
            top = rect.top - widgetH - 6;
        }
        // Clamp right if overflows viewport right
        if (left + widgetW > window.innerWidth) {
            left = window.innerWidth - widgetW - 8;
        }
        if (left < 0) left = 8;

        widget.style.top  = top  + 'px';
        widget.style.left = left + 'px';
    };

    // ── Dispatch events ───────────────────────────────────────────────────
    VanillaPicker.prototype._dispatch = function(eventName) {
        var evt = new CustomEvent(eventName, {
            bubbles: true,
            detail: { date: this._currentDate }
        });
        this._containerEl.dispatchEvent(evt);
    };

    // ── setMinDate ─────────────────────────────────────────────────────────
    VanillaPicker.prototype.setMinDate = function(date) {
        this._minDate = date ? new this._Date(date) : null;
        if (this._isOpen) this._renderCalendar();
    };

    VanillaPicker.prototype.setLimits = function(min, max, validate) {
        if (validate) this._options.validate = validate;
        this._minDate = min ? new this._Date(min) : null;
        this._maxDate = max ? new this._Date(max) : null;
        if (this._isOpen) this._renderCalendar();
    };

    // ── setRange ──────────────────────────────────────────────────────────
    VanillaPicker.prototype.setRange = function(start, end) {
        this._rangeStart = start || null;
        this._rangeEnd   = end   || null;
        if (this._isOpen) this._renderCalendar();
    };

    // ── Parse "yyyy-MM-dd HH:mm" string ────────────────────────────────
    function parseDateTime(str, DateType) {
        var m = String(str).match(/^(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})$/);
        if (!m) return null;
        var d = new DateType(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
        if (isNaN(d.getTime()) || d.getFullYear() !== +m[1] || d.getMonth() !== +m[2] - 1 || d.getDate() !== +m[3] || d.getHours() !== +m[4] || d.getMinutes() !== +m[5]) return null;
        return d;
    }

    // ── Toggle listeners ──────────────────────────────────────────────────
    VanillaPicker.prototype._attachToggleListeners = function() {
        var self = this;

        // Calendar icon toggles the picker
        this._containerEl.addEventListener('click', function(e) {
            if (e.target.closest('.td-toggle')) {
                if (self._isOpen) self.hide(); else self.show();
            }
        });

        // Keyboard input: parse on blur or Enter
        if (this._input) {
            this._input.addEventListener('input', function() {
                self._input.classList.remove('is-placeholder');
            });
            this._input.addEventListener('blur', function() {
                self._applyInputValue();
            });
            this._input.addEventListener('keydown', function(e) {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    self._applyInputValue();
                    if (self._isOpen) self.hide();
                    self._input.blur();
                }
            });
        }
    };

    VanillaPicker.prototype._applyInputValue = function() {
        var raw = this._input.value.trim();
        if (!raw) return;
        var d = parseDateTime(raw, this._Date);
        if (d) {
            var today = this._options.now ? this._options.now() : new this._Date();
            var todayStart = new this._Date(today.getFullYear(), today.getMonth(), today.getDate());
            var maxDate = this._maxDate || new this._Date(today.getFullYear(), today.getMonth() + 2, today.getDate() - 1);
            if (d < (this._minDate || todayStart) || d > maxDate) {
                if (this._options.validate) this._input.setCustomValidity('예약 가능한 날짜 범위를 확인해주세요.');
                else this._updateInput();
                return;
            }
            this._setValue(d);
        } else {
            // Keep invalid input visible for configured validators.
            if (this._options.validate) this._input.setCustomValidity('올바른 일시를 입력해주세요.');
            else this._updateInput();
        }
    };

    // ── Expose globals ────────────────────────────────────────────────────
    VanillaPicker.WallClockDate = WallClockDate;
    global.VanillaPicker          = VanillaPicker;
    global.VanillaPickerNamespace = VanillaPickerNamespace;

}(window));
