FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg chromium ca-certificates fonts-liberation && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY app.py native_engine.py search_engine.py vpn_linux.py ./
COPY templates ./templates
COPY static ./static
COPY tests_smoke.py ./
RUN python -m py_compile app.py native_engine.py search_engine.py vpn_linux.py tests_smoke.py && python -m unittest tests_smoke -v
EXPOSE 10000
CMD ["sh","-c","exec gunicorn --bind 0.0.0.0:${PORT:-10000} --workers 1 --threads 6 --timeout 240 app:app"]
