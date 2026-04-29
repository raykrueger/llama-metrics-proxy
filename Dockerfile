FROM python:3.12-slim
WORKDIR /app
COPY scraper.py .
ENTRYPOINT ["python3", "scraper.py"]
