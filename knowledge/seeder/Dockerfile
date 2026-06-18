FROM python:3.11-slim
WORKDIR /app
COPY shared/requirements.txt /app/shared/requirements.txt
RUN pip install --no-cache-dir -r /app/shared/requirements.txt
COPY knowledge/seeder/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY shared/ /app/shared/
COPY knowledge/ /app/knowledge/
ENV PYTHONPATH=/app
CMD ["python", "knowledge/seeder/src/main.py"]
