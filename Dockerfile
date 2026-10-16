FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8097

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates ./templates
COPY static ./static

RUN useradd --system --uid 10001 vault && chown -R vault /app
USER vault

EXPOSE 8097
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8097'), timeout=4)"

# --preload runs db.create_all() once before forking workers
CMD ["sh", "-c", "exec gunicorn --preload --workers 2 --bind 0.0.0.0:${PORT} --access-logfile - app:app"]
