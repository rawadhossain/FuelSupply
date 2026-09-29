FROM python:3.11-slim-bookworm
RUN useradd -m -u 1000 chaos
WORKDIR /app
COPY proxy.py .
USER chaos
EXPOSE 8000 8001
CMD ["python", "proxy.py"]
