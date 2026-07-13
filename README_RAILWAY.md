Railway deployment notes

Quick start

- Set environment variables in Railway project:
  - `TOKEN` — Discord bot token (if you intend to start a bot)
  - `DISCORD_WEBHOOK_URL` — webhook URL for alerts/signals
  - `START_DISCORD_BOT` — set `true` only if you want the bot to log in
  - `PORT` — optional (default 8080)

Services
- `web` (Procfile): `python bot.py` — runs the full bot with HTTP health endpoint.
- `signals` (Procfile): `python bot.py --signals-only` — runs the signal generator once and exits. Use Railway Scheduled Jobs to run this periodically (e.g., every hour).

Notes
- The bot persists ~48 hours of price data in the `data/` folder (writeable on the container filesystem). For persistence across restarts, connect a volume or external storage.
- Keep your `TOKEN` and `DISCORD_WEBHOOK_URL` secure via Railway environment settings.
