FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt

WORKDIR /app

# 사내망 TLS 가로채기 루트 인증서. 빌드 때 --build-context corpca=<인증서 폴더>로 넘긴다.
COPY --from=corpca corp-root.crt /usr/local/share/ca-certificates/corp-root.crt
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libnss3-tools xvfb x11vnc novnc \
    && update-ca-certificates \
    && mkdir -p /root/.pki/nssdb \
    && certutil -d sql:/root/.pki/nssdb -N --empty-password \
    && certutil -d sql:/root/.pki/nssdb -A -t "C,," -n corp-root -i /usr/local/share/ca-certificates/corp-root.crt \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY . .

ENV DISPLAY=:99
EXPOSE 9000 6080

# 김포 조회는 화면 있는 Chromium을 띄우므로 가상 디스플레이(Xvfb) 위에서 실행한다.
# xvfb-run은 PID 1에서 Xvfb 준비 신호를 받지 못해 멈추므로 Xvfb를 직접 띄운다.
# 가상 화면의 Chromium 창(김포 결제)은 noVNC(6080)로 브라우저에서 본다. 비밀번호가 없으니 6080은 127.0.0.1에만 연다.
CMD ["sh", "-c", "Xvfb :99 -screen 0 1280x1024x24 -nolisten tcp & sleep 1; x11vnc -display :99 -forever -shared -nopw -localhost -rfbport 5900 -quiet & websockify --web /usr/share/novnc 6080 localhost:5900 & exec flask --app app run --host 0.0.0.0 --port 9000"]
