# Intelligence Service image. Build context = repo root:
#   docker build -f intelligence/Dockerfile -t fuel-intelligence .
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY intelligence/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY shared/ /app/shared/
COPY intelligence/ /app/intelligence/
EXPOSE 8100
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8100/health').status==200 else 1)"
CMD ["uvicorn", "intelligence.service:app", "--host", "0.0.0.0", "--port", "8100"]
