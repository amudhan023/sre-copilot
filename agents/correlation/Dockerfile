FROM python:3.11-slim
WORKDIR /app
COPY shared/requirements.txt /app/shared/requirements.txt
RUN pip install --no-cache-dir -r /app/shared/requirements.txt
COPY agents/correlation/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY shared/ /app/shared/
COPY agents/correlation/ /app/agents/correlation/
ENV PYTHONPATH=/app
CMD ["python", "agents/correlation/src/main.py"]
