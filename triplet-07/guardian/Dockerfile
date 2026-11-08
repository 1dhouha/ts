FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/src:/app
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# run as a normal user, not root: a bug in Guardian must not become root in the container
RUN useradd --create-home --uid 10001 guardian && mkdir -p /app/logs && chown -R guardian /app/logs
USER guardian
EXPOSE 8002
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8002/health', timeout=2).status==200 else 1)"
CMD ["uvicorn", "--factory", "guardian.main:app", "--host", "0.0.0.0", "--port", "8002", "--no-access-log"]
