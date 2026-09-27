FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir gunicorn

COPY . .

ENV PYTHONUNBUFFERED=1

CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app
