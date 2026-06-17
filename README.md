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
python3 -m pip install -r requirement.txt
pytest -q
```

