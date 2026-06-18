FROM python:3.11-slim
WORKDIR /app
COPY shared/requirements.txt /app/shared/requirements.txt
RUN pip install --no-cache-dir -r /app/shared/requirements.txt
COPY simulation/deployment-simulator/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY shared/ /app/shared/
COPY simulation/deployment-simulator/ /app/simulation/deployment-simulator/
ENV PYTHONPATH=/app
CMD ["python", "simulation/deployment-simulator/src/main.py"]
