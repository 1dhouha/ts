FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000 \
    TWIN_LOG_DIR=/app/logs

WORKDIR /app

RUN groupadd --system --gid 10001 twin \
    && useradd --uid 10001 --gid twin --home-dir /app --no-create-home \
       --shell /usr/sbin/nologin twin

COPY requirements.lock ./
COPY src/ ./src/
COPY scenarios/ ./scenarios/

RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock \
    && mkdir -p /app/logs \
    && chown 10001:10001 /app/logs

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT', '8000') + '/health/ready', timeout=3).read()"]

CMD ["python", "src/twin_app.py"]
