# Trainer-bot-2
Bot in training

## Run

Start the bot locally:

```bash
python3 bot.py
```

The health endpoint is available at `http://localhost:8080/status` and returns JSON with `price`, `rsi`, `direction`, etc.

## Tests

Install dependencies and run tests:

```bash
python3 -m pip install -r requirements.txt
pytest -q
```

## Deployment / Supervisor

Systemd service example (create `/etc/systemd/system/trainer-bot.service`):

```
[Unit]
Description=Trainer-bot service
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/Trainer-bot-2
ExecStart=/usr/bin/env python3 bot.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Then enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now trainer-bot.service
```

Dockerfile example:

```
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app
EXPOSE 8080
CMD ["python3", "bot.py"]
```

Docker run (map host port if desired):

```bash
docker build -t trainer-bot .
docker run -d -p 8080:8080 --name trainer-bot trainer-bot
```

Docker Compose example:

```yaml
version: "3.8"
services:
  trainer-bot:
    build: .
    ports:
      - "8080:8080"
    environment:
      - PORT=8080
      - STATUS_TOKEN=your-secret-token
      - DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/your-hook
    restart: unless-stopped
```

Start with:

```bash
docker compose up --build -d
```

Notes:
- The bot will attempt to bind `PORT` (default 8080) and will try the next 9 ports if the base port is unavailable.
- When deploying under a process manager, prefer using the `PORT` env var to control the listen port.
- Set `DISCORD_WEBHOOK_URL` to your webhook URL for alert delivery. It must be in the format `https://discord.com/api/webhooks/<webhook_id>/<webhook_token>`.
- Do not use a channel URL such as `https://discord.com/channels/...` or a webhook viewer URL; those will return 401/405 errors.
- Set `STATUS_TOKEN` to protect the `/status` endpoint via `Authorization: Bearer <STATUS_TOKEN>` or `?token=<STATUS_TOKEN>`.
- Use `DATA_SOURCE=coingecko` when running in Railway or other restricted environments to avoid yfinance rate limits.
- By default, the bot now uses `coingecko` first and falls back to `yfinance` only if CoinGecko fails.
- Logs are written to the `logs/` directory with rotation (`logs/bot.log`). Configure your log collector or mount a volume when using Docker.

### Deployment

- Use `Procfile` for Heroku/Railway-style deployment.
- Use `Dockerfile` to build a container image locally or in your platform.
- The `requirements.txt` file is the canonical dependency manifest used in CI and deployment.


