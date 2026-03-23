FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    XRAY_WEB_DATA_DIR=/data/xray1 \
    XRAY_BIN=/data/xray1/xray \
    XRAY_TEST_URL=https://www.google.com/generate_204 \
    XRAY_TEST_WORKERS=8 \
    XRAY_TEST_TIMEOUT=6 \
    XRAY_ADMIN_USER=admin \
    XRAY_ADMIN_PASS=admin123 \
    XRAY_AUTH_SALT=xray-web \
    XRAY_LOGIN_MAX_ATTEMPTS=5 \
    XRAY_LOGIN_WINDOW_MIN=10 \
    XRAY_LOGIN_LOCK_MIN=15 \
    XRAY_AUTO_CHECK_INTERVAL=30

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY app/ /app/
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

RUN pip install --no-cache-dir -U fastapi uvicorn[standard] jinja2 python-multipart pyyaml

VOLUME ["/data/xray1"]

EXPOSE 8088

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8088"]
