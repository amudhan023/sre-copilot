FROM python:3.11-slim
WORKDIR /app
COPY shared/requirements.txt /app/shared/requirements.txt
RUN pip install --no-cache-dir -r /app/shared/requirements.txt
COPY application/sre-api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY shared/ /app/shared/
COPY application/sre-api/ /app/application/sre-api/
ENV PYTHONPATH=/app
CMD ["uvicorn", "application.sre_api.src.main:app", "--host", "0.0.0.0", "--port", "8000"]
