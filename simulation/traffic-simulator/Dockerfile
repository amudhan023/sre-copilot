FROM python:3.11-slim
WORKDIR /app
COPY shared/requirements.txt /app/shared/requirements.txt
RUN pip install --no-cache-dir -r /app/shared/requirements.txt
COPY simulation/traffic-simulator/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY shared/ /app/shared/
COPY simulation/traffic-simulator/ /app/simulation/traffic-simulator/
ENV PYTHONPATH=/app
CMD ["python", "simulation/traffic-simulator/src/main.py"]
