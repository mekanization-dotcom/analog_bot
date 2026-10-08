FROM python:3.13-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8080
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["gunicorn","--bind","0.0.0.0:8080","--workers","1","--threads","4","--timeout","120","motherbot:app"]
