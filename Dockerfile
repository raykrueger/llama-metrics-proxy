FROM python:3.13-slim
WORKDIR /app
COPY scraper.py .
ENTRYPOINT ["python3", "scraper.py"]
