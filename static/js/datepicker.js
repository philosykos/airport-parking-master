(function(global) {
    'use strict';

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
        return new Date(year, month + 1, 0).getDate();
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
        this._currentDate  = this._options.defaultDate
            ? new Date(this._options.defaultDate)
            : new Date();
        this._viewDate     = new Date(this._currentDate);
        this._isOpen       = false;
        this._rangeStart   = null;
        this._rangeEnd     = null;
        this._minDate      = null;
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
            var newDate = new Date(self._currentDate);
            newDate.setFullYear(year);
            newDate.setMonth(month);
            newDate.setDate(day);
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
        var today   = new Date();
        var selected = this._currentDate;

        // Update header text
        var headerDate = new Date(year, month, 1);
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
        var firstDay = new Date(year, month, 1).getDay();
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
        var todayStart = new Date(today.getFullYear(), today.getMonth(), today.getDate());
        var maxDate = new Date(today.getFullYear(), today.getMonth() + 2, today.getDate() - 1);
        var minDate = this._minDate
            ? new Date(this._minDate.getFullYear(), this._minDate.getMonth(), this._minDate.getDate())
            : todayStart;
        for (var d = 1; d <= totalDays; d++) {
            var classes = '';
            var cellDate = new Date(year, month, d);
            if (cellDate < minDate || cellDate > maxDate) classes += ' disabled';
            if (isSameDay(cellDate, today))    classes += ' today';
            if (isSameDay(cellDate, selected)) classes += ' active';
            if (rangeStart && isSameDay(cellDate, rangeStart)) classes += ' range-start';
            if (rangeEnd   && isSameDay(cellDate, rangeEnd))   classes += ' range-end';
            if (rangeStart && rangeEnd) {
                var ts = cellDate.getTime();
                var rs = new Date(rangeStart.getFullYear(), rangeStart.getMonth(), rangeStart.getDate()).getTime();
                var re = new Date(rangeEnd.getFullYear(),   rangeEnd.getMonth(),   rangeEnd.getDate()).getTime();
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
        el.textContent = day;
        el.dataset.year  = year;
        el.dataset.month = month;
        el.dataset.day   = day;
        return el;
    };

    // ── setValue ──────────────────────────────────────────────────────────
    VanillaPicker.prototype._setValue = function(date) {
        var d = (date instanceof Date) ? date : new Date(date);
        this._currentDate = d;
        this._viewDate    = new Date(d);
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
        this._minDate = date ? new Date(date) : null;
        if (this._isOpen) this._renderCalendar();
    };

    // ── setRange ──────────────────────────────────────────────────────────
    VanillaPicker.prototype.setRange = function(start, end) {
        this._rangeStart = start || null;
        this._rangeEnd   = end   || null;
        if (this._isOpen) this._renderCalendar();
    };

    // ── Parse "yyyy-MM-dd HH:mm" string ────────────────────────────────
    function parseDateTime(str) {
        var m = String(str).match(/^(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})$/);
        if (!m) return null;
        var d = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
        if (isNaN(d.getTime())) return null;
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
        var d = parseDateTime(raw);
        if (d) {
            var today = new Date();
            var todayStart = new Date(today.getFullYear(), today.getMonth(), today.getDate());
            var maxDate = new Date(today.getFullYear(), today.getMonth() + 2, today.getDate() - 1);
            if (d < todayStart || d > maxDate) {
                this._updateInput();
                return;
            }
            this._setValue(d);
        } else {
            // Revert to current valid value
            this._updateInput();
        }
    };

    // ── Expose globals ────────────────────────────────────────────────────
    global.VanillaPicker          = VanillaPicker;
    global.VanillaPickerNamespace = VanillaPickerNamespace;

}(window));
