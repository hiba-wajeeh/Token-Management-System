FROM python:3.11-slim

WORKDIR /app

COPY requirements-server.txt .

RUN pip install --no-cache-dir -r requirements-server.txt

COPY . .

WORKDIR /app/server

EXPOSE 8032

CMD ["python", "server5.py"]