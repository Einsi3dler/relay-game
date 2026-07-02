import asyncio
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
import websockets


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def live_server():
    port = _free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    ws_url = f"ws://127.0.0.1:{port}"
    try:
        _wait_for_server(base_url)
        yield base_url, ws_url
    finally:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)


def test_websocket_join_and_puzzle_submission_broadcasts_state(live_server):
    asyncio.run(_join_and_submit(live_server))


def test_websocket_sabotage_message_reaches_target_team(live_server):
    asyncio.run(_sabotage_reaches_target(live_server))


async def _join_and_submit(live_server):
    base_url, ws_url = live_server
    match_id = httpx.post(f"{base_url}/api/matches", json={}, timeout=5).json()["match"]["id"]
    joined = httpx.post(
        f"{base_url}/api/matches/{match_id}/join",
        json={"name": "Socket", "team_id": "alpha", "role": "Oracle"},
        timeout=5,
    ).json()
    player_id = joined["player"]["id"]

    async with websockets.connect(f"{ws_url}/ws/matches/{match_id}?player_id={player_id}") as ws:
        initial = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        assert initial["type"] == "state_snapshot"
        puzzle = initial["state"]["me"]["current_puzzle"]
        answer = _oracle_answer(puzzle["prompt"])
        await ws.send(json.dumps({"type": "submit_puzzle", "puzzle_id": puzzle["id"], "answer": answer}))

        snapshot = await _receive_type(ws, "state_snapshot")
        assert snapshot["state"]["me"]["status"] in {"active", "grinding"}


async def _sabotage_reaches_target(live_server):
    base_url, ws_url = live_server
    match_id = httpx.post(f"{base_url}/api/matches", json={}, timeout=5).json()["match"]["id"]
    attacker = httpx.post(
        f"{base_url}/api/matches/{match_id}/join",
        json={"name": "Attacker", "team_id": "alpha", "role": "Saboteur"},
        timeout=5,
    ).json()["player"]
    httpx.post(
        f"{base_url}/api/matches/{match_id}/join",
        json={"name": "Blocker", "team_id": "alpha", "role": "Terminal"},
        timeout=5,
    )
    defender = httpx.post(
        f"{base_url}/api/matches/{match_id}/join",
        json={"name": "Defender", "team_id": "bravo", "role": "Warden"},
        timeout=5,
    ).json()["player"]

    async with websockets.connect(f"{ws_url}/ws/matches/{match_id}?player_id={attacker['id']}") as ws_a:
        async with websockets.connect(f"{ws_url}/ws/matches/{match_id}?player_id={defender['id']}") as ws_b:
            snapshot = await _receive_type(ws_a, "state_snapshot")
            await _receive_type(ws_b, "state_snapshot")
            await _earn_attack(ws_a, snapshot, "dim")
            await ws_a.send(json.dumps({"type": "deploy_powerup", "powerup": "dim", "target_team_id": "bravo"}))

            sabotage = await _receive_type(ws_b, "sabotage_applied")
            assert sabotage["effect"] == "dim"
            assert sabotage["target_team_id"] == "bravo"


async def _earn_attack(ws, snapshot, powerup):
    puzzle = snapshot["state"]["me"]["current_puzzle"]
    await ws.send(
        json.dumps(
            {
                "type": "submit_puzzle",
                "puzzle_id": puzzle["id"],
                "answer": _answer_for_prompt(puzzle["prompt"]),
            }
        )
    )
    snapshot = await _snapshot_with_status(ws, "grinding")
    points = snapshot["state"]["teams"][snapshot["state"]["me"]["team_id"]]["points"]
    while points < 25:
        grind = snapshot["state"]["me"]["current_grind"]
        await ws.send(
            json.dumps(
                {
                    "type": "submit_grind",
                    "puzzle_id": grind["id"],
                    "answer": _answer_for_prompt(grind["prompt"]),
                }
            )
        )
        snapshot = await _receive_type(ws, "state_snapshot")
        points = snapshot["state"]["teams"][snapshot["state"]["me"]["team_id"]]["points"]
    await ws.send(json.dumps({"type": "buy_powerup", "powerup": powerup}))
    await _receive_type(ws, "state_snapshot")


async def _snapshot_with_status(ws, status):
    for _ in range(20):
        snapshot = await _receive_type(ws, "state_snapshot")
        if snapshot["state"]["me"]["status"] == status:
            return snapshot
    raise AssertionError(f"Did not reach status {status}.")


async def _receive_type(ws, expected_type):
    for _ in range(20):
        message = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        if message["type"] == expected_type:
            return message
    raise AssertionError(f"Did not receive {expected_type}.")


def _oracle_answer(prompt):
    value = int(prompt.split()[3])
    return "true" if value % 2 == 0 else "false"


def _answer_for_prompt(prompt):
    if "True or false" in prompt:
        return _oracle_answer(prompt)
    if "Offense timing drill" in prompt:
        return prompt.split("set: ", 1)[1].split(",", 1)[0]
    if "Grind pulse" in prompt:
        left, op, right = prompt.rsplit(" ", 3)[1:]
        left_value = int(left)
        right_value = int(right)
        if op == "+":
            return str(left_value + right_value)
        if op == "-":
            return str(left_value - right_value)
        return str(left_value * right_value)
    raise AssertionError(f"No answer parser for prompt: {prompt}")


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_server(base_url):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{base_url}/api/config", timeout=0.5)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.1)
    raise RuntimeError("Uvicorn test server did not start.")
