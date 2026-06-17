#!/usr/bin/env bash
set -euo pipefail

if [ ! -f requirements.txt ]; then
  echo "requirements.txt not found"
  exit 1
fi

echo "Building Docker image..."
docker build -t trainer-bot .

echo "Starting Docker Compose..."
docker compose up --build -d

echo "Deployment complete."
echo "Visit http://localhost:8080/status or check container logs with docker compose logs -f"
