FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create the data directory for SQLite volume mount
RUN mkdir -p /data

# Default command — overridden per service in fly.toml
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
