FROM python:3.11-slim
WORKDIR /app
COPY shared/requirements.txt /app/shared/requirements.txt
RUN pip install --no-cache-dir -r /app/shared/requirements.txt
COPY simulation/failure-injector/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY shared/ /app/shared/
COPY simulation/failure-injector/ /app/simulation/failure-injector/
ENV PYTHONPATH=/app
CMD ["python", "simulation/failure-injector/src/main.py"]
