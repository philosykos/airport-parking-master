(() => {
    const labels = Object.freeze({PENDING: '전송 대기', SENDING: '전송 중', RETRYING: '재시도 중',
        SENT: '전송 완료', FAILED: '전송 실패', UNKNOWN: '전송 결과 확인 필요', DISABLED: '알림 꺼짐', CANCELLED: '전송 취소'});
    const status = Object.freeze({label: value => labels[value] || '상태 확인 필요'});
    window.NotificationStatus = status;
    (window.UI = window.UI || {}).notificationStatus = status;
})();
