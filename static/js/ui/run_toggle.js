/* 시작·중지 버튼은 폼 첫 칸 한 자리에서 하나만 보인다. 누른 버튼이 사라지면 새로 보인 버튼이
   누를 수 있게 되는 즉시 포커스를 옮긴다. 사용자가 다른 곳으로 포커스를 옮겼으면 옮기지 않는다. */
(() => {
    'use strict';
    window.UI = window.UI || {};
    class RunToggle {
        constructor(start, stop) {
            this.start = start;
            this.stop = stop;
            this.pressed = null;
            for (const button of [start, stop]) button.addEventListener('click', () => { this.pressed = button; });
            document.addEventListener('focusin', event => {
                if (event.target !== start && event.target !== stop) this.pressed = null;
            });
        }
        set(running) {
            const shown = running ? this.stop : this.start;
            const gone = running ? this.start : this.stop;
            gone.hidden = true;
            shown.hidden = false;
            if (this.pressed === gone && !shown.disabled) {
                this.pressed = null;
                shown.focus();
            }
        }
    }
    UI.RunToggle = RunToggle;
})();
