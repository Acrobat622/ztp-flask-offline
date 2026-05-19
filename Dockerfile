FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8080

CMD ["gunicorn", "-b", "0.0.0.0:8080", "--workers", "2", "--threads", "2", "--timeout", "900", "app:app"]
