# One image, two entrypoints (api / worker). Based on the Playwright image so the worker has Chromium and its
# system libraries; the API just doesn't launch a browser.
FROM mcr.microsoft.com/playwright/python:v1.64.1-noble

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --break-system-packages -r requirements.txt
COPY portalpilot ./portalpilot

RUN useradd -m -u 10001 app
USER app
EXPOSE 8000
CMD ["uvicorn", "portalpilot.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
