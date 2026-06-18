FROM python:3.11-slim
WORKDIR /app
COPY shared/requirements.txt /app/shared/requirements.txt
RUN pip install --no-cache-dir -r /app/shared/requirements.txt
COPY ingestion/log-ingester/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY shared/ /app/shared/
COPY ingestion/log-ingester/ /app/ingestion/log-ingester/
ENV PYTHONPATH=/app
CMD ["python", "ingestion/log-ingester/src/main.py"]
