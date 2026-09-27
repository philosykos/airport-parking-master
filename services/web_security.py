"""앱 전체 요청 보호: Host 제한, 다른 출처의 상태 변경 요청 거부, 공통 보안 헤더(CSP 포함).

이 앱은 로그인이 없는 로컬 전용 서비스라, 요청이 이 PC 브라우저의 이 앱 화면에서 왔는지가 유일한 경계다.
Host 제한은 DNS 리바인딩(외부 도메인을 127.0.0.1로 돌려 같은 출처처럼 접근)을 막고,
출처 검사는 다른 사이트가 폼·fetch로 보내는 POST(CSRF)를 막는다.
"""
from urllib.parse import urlsplit

from flask import jsonify, request

TRUSTED_HOSTS = ["localhost", "127.0.0.1"]
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# 스크립트는 같은 출처 파일만 실행한다(인라인 onclick 금지). 화면이 외부 응답 문구를 표시하므로 이스케이프가
# 빠졌을 때의 2차 방어다. 스타일은 템플릿과 JS가 style 속성을 써서 'unsafe-inline'을 둔다.
# 외부 CSS·폰트는 jsDelivr(Pretendard)와 Google Fonts만 받는다.
CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com",
    "font-src 'self' data: https://cdn.jsdelivr.net https://fonts.gstatic.com",
    "img-src 'self' data:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])
COMMON_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": CSP,
}
CROSS_SITE_ERROR = "다른 사이트에서 온 요청은 받지 않습니다."


def is_cross_site(req):
    """브라우저가 붙이는 Sec-Fetch-Site·Origin 헤더로 다른 출처 요청을 가려낸다.

    두 헤더가 모두 없으면(curl 등 브라우저 밖 클라이언트) 허용한다. 브라우저는 다른 출처로 POST할 때 Origin을 항상 붙인다.
    """
    site = req.headers.get("Sec-Fetch-Site")
    if site is not None:
        return site not in ("same-origin", "none")
    origin = req.headers.get("Origin")
    if origin is None:
        return False
    parts = urlsplit(origin)
    return (parts.scheme, parts.netloc) != (req.scheme, req.host)


def init_app(app):
    app.config["TRUSTED_HOSTS"] = TRUSTED_HOSTS  # Flask 3.1+: 목록 밖 Host는 400

    @app.before_request
    def reject_cross_site():
        if request.method in UNSAFE_METHODS and is_cross_site(request):
            return jsonify({"error": CROSS_SITE_ERROR}), 403

    @app.after_request
    def set_common_headers(response):
        for name, value in COMMON_HEADERS.items():
            response.headers.setdefault(name, value)
        return response
