FROM python:3.12-slim

WORKDIR /app
COPY workers/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY workers /app/workers

CMD ["python", "workers/worker.py"]
