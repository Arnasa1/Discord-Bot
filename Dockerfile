# Use official Python slim image
FROM python:3.11-slim

# Install FFmpeg, build tools, and SSL certificates
RUN apt-get update && \
    apt-get install -y \
        ffmpeg \
        gcc \
        libffi-dev \
        python3-dev \
        musl-dev \
        ca-certificates \
    && apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all project files (including bot.py, youtube_cookies.txt if present)
COPY . .

# Expose port for Flask keep-alive
EXPOSE 8080

# Set SSL_CERT_FILE environment (optional for local + Windows compatibility)
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

# Start the bot
CMD ["python", "bot.py"]
