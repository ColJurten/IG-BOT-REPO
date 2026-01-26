FROM python:3.11-slim

WORKDIR /app

# Create data directory
RUN mkdir -p /app/data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

EXPOSE 5000

# Use Python directly to ensure polling starts
CMD ["python", "-u", "app/main.py"]