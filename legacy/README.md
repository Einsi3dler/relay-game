# The Relay MVP

A browser-playable MVP for a synchronous multiplayer relay puzzle game.

## Run

```bash
python3 -m pip install -e ".[test]"
python3 -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000` in two browser tabs and join opposing teams.

## Test

```bash
python3 -m pytest
```
