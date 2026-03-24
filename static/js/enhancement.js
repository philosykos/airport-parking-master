(function() {
    var _originalShowToast = showToast;
    showToast = function(message, type) {
        _originalShowToast(message, type);
        if (type === 'success' && message === '예약이 완료되었습니다') {
            var overlay = document.getElementById('success-overlay');
            if (overlay) {
                overlay.classList.remove('hidden');
            }
        }
    };

    // Also close overlay with Escape key
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            var overlay = document.getElementById('success-overlay');
            if (overlay && !overlay.classList.contains('hidden')) {
                overlay.classList.add('hidden');
            }
        }
    });
})();
