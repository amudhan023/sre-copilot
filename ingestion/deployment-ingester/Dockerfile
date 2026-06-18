FROM python:3.11-slim
WORKDIR /app
COPY shared/requirements.txt /app/shared/requirements.txt
RUN pip install --no-cache-dir -r /app/shared/requirements.txt
COPY ingestion/deployment-ingester/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY shared/ /app/shared/
COPY ingestion/deployment-ingester/ /app/ingestion/deployment-ingester/
ENV PYTHONPATH=/app
CMD ["python", "ingestion/deployment-ingester/src/main.py"]
