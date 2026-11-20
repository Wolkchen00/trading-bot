# v5 image: the new engine and nothing else (no v4 module, no model download, no secrets).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Exact versions only (plan principle P9).
COPY requirements-v5.lock.txt /app/requirements-v5.lock.txt
RUN pip install --no-cache-dir -r /app/requirements-v5.lock.txt

COPY v5/ /app/v5/
COPY tools/v5_durum.py tools/v5_tatbikat.py /app/tools/
COPY tests_v5/ /app/tests_v5/
COPY ops/ /app/ops/
# The packaging files themselves, so the test suite can check them inside the image too.
COPY Dockerfile docker-compose.yml .dockerignore /app/

RUN mkdir -p /app/logs

# No health check on purpose: a blocked state must report itself, not restart the container.
CMD ["python", "-u", "-m", "v5.runner"]
