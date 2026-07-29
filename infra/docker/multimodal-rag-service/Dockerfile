FROM python:3.12-slim

WORKDIR /app
COPY services/multimodal-rag-service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY services/multimodal-rag-service/app /app/app

EXPOSE 8004
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8004"]
