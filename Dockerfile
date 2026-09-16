# Playwright-versionen i basimagen MÅSTE matcha playwright i requirements.txt
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    TZ=Europe/Stockholm

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY scout/ scout/

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

# En worker: schemaläggaren körs i samma process
CMD ["uvicorn", "scout.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
