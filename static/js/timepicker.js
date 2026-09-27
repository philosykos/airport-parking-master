function createScrollTimePicker(tdInstance, pickerElId, minuteStep) {
    var ITEM_HEIGHT = 36;
    var VISIBLE_ITEMS = 5;
    var MINUTE_STEP = minuteStep || 5;

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
            var d = new tdInstance._Date(current);
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
            var widget = tdInstance._widget;
            if (!tdInstance._isOpen) return;
            // Hide TD's built-in time container
            var timeContainer = widget.querySelector('.time-container');
            if (timeContainer) timeContainer.style.display = 'none';
            // Append custom scroll picker if not already there
            if (!widget.querySelector('.td-scroll-time')) {
                widget.classList.add('has-scroll-time');
                widget.appendChild(container);
            }
            tdInstance._position();
            // Sync scroll position from current date
            var current = tdInstance.dates.lastPicked;
            if (current) {
                var d = new tdInstance._Date(current);
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

