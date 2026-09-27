// 버튼 누른 자리 물결 효과(form.css .btn-ripple). 액션 버튼에만 준다.
(() => {
    'use strict';
    const TARGET = '.btn-start, .btn-stop, .btn-test, .btn-clear';
    document.addEventListener('click', event => {
        const button = event.target.closest(TARGET);
        if (!button || button.disabled) return;
        const rect = button.getBoundingClientRect();
        const ripple = document.createElement('span');
        ripple.className = 'btn-ripple';
        ripple.style.left = (event.clientX - rect.left) + 'px';
        ripple.style.top = (event.clientY - rect.top) + 'px';
        button.append(ripple);
        setTimeout(() => ripple.remove(), 600);
    });
})();
