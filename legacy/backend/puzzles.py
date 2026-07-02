from __future__ import annotations

import random
from dataclasses import dataclass

from backend.models import Puzzle, ROLES


@dataclass(frozen=True)
class PuzzleResult:
    puzzle: Puzzle


class PuzzleRegistry:
    def create_role_puzzle(
        self, role: str, level: int, player_id: str, seed_offset: int = 0
    ) -> Puzzle:
        if role == "Wildcard":
            roles = [item for item in ROLES if item not in {"Wildcard", "Saboteur", "Warden"}]
            chosen = roles[self._seed(player_id, level, seed_offset) % len(roles)]
            return self.create_role_puzzle(chosen, level, player_id, seed_offset + 17)

        handlers = {
            "Terminal": self._terminal,
            "Architect": self._architect,
            "Vault": self._vault,
            "Oracle": self._oracle,
            "Wordsmith": self._wordsmith,
            "Quant": self._quant,
            "Maestro": self._maestro,
            "Saboteur": self._saboteur,
            "Warden": self._warden,
        }
        return handlers.get(role, self._oracle)(level, player_id, seed_offset)

    def create_grind_task(self, role: str, player_id: str, count: int) -> Puzzle:
        seed = self._seed(player_id, count, 91)
        left = 4 + seed % 13
        right = 3 + (seed // 3) % 11
        op = ["+", "-", "*"][seed % 3]
        if op == "+":
            answer = left + right
        elif op == "-":
            answer = left - right
        else:
            answer = left * right
        return Puzzle(
            id=f"grind-{player_id}-{count}",
            role=role,
            kind="grind",
            prompt=f"Grind pulse: solve {left} {op} {right}",
            answer=str(answer),
            level=0,
            payload={"reward": 8 + seed % 9},
        )

    def create_backlog_puzzle(self, role: str, player_id: str, team_level: int) -> Puzzle:
        seed = self._seed(player_id, team_level, 211)
        code = "".join(str((seed + index * 7) % 10) for index in range(4))
        return Puzzle(
            id=f"backlog-{player_id}-{team_level}-{seed % 1000}",
            role=role,
            kind="backlog",
            prompt=f"Backlog sync: type the recovery code {code}",
            answer=code,
            level=team_level,
            payload={"seconds": 20},
        )

    def _terminal(self, level: int, player_id: str, seed_offset: int) -> Puzzle:
        seed = self._seed(player_id, level, seed_offset)
        port = 3000 + seed % 700
        target = ["auth", "cache", "relay", "vault"][seed % 4]
        return Puzzle(
            id=self._id("terminal", player_id, level, seed_offset),
            role="Terminal",
            kind="logic",
            prompt=f'Broken JSON route: {{"service":"{target}","port":{port},"ok":tru}}. Fix the boolean token.',
            answer="true",
            level=level,
            payload={"hint": "Return the corrected token only."},
        )

    def _architect(self, level: int, player_id: str, seed_offset: int) -> Puzzle:
        seed = self._seed(player_id, level, seed_offset)
        nodes = ["A", "B", "C", "D"]
        random.Random(seed).shuffle(nodes)
        answer = "-".join(sorted(nodes))
        return Puzzle(
            id=self._id("architect", player_id, level, seed_offset),
            role="Architect",
            kind="spatial",
            prompt=f"Order the grid anchors alphabetically to stabilize the route: {' '.join(nodes)}",
            answer=answer,
            level=level,
            payload={"format": "A-B-C-D"},
        )

    def _vault(self, level: int, player_id: str, seed_offset: int) -> Puzzle:
        seed = self._seed(player_id, level, seed_offset)
        symbols = ["sun", "key", "wave", "ring", "star"]
        sequence = [symbols[(seed + index) % len(symbols)] for index in range(3)]
        return Puzzle(
            id=self._id("vault", player_id, level, seed_offset),
            role="Vault",
            kind="memory",
            prompt=f"Memorize and repeat the vault sequence: {' / '.join(sequence)}",
            answer=" ".join(sequence),
            level=level,
            payload={"sequence": sequence},
        )

    def _oracle(self, level: int, player_id: str, seed_offset: int) -> Puzzle:
        seed = self._seed(player_id, level, seed_offset)
        value = 11 + seed % 40
        answer = "true" if value % 2 == 0 else "false"
        return Puzzle(
            id=self._id("oracle", player_id, level, seed_offset),
            role="Oracle",
            kind="trivia",
            prompt=f"True or false: {value} is an even number.",
            answer=answer,
            level=level,
            payload={"options": ["true", "false"]},
        )

    def _wordsmith(self, level: int, player_id: str, seed_offset: int) -> Puzzle:
        words = ["signal", "relay", "cipher", "origin", "vector"]
        seed = self._seed(player_id, level, seed_offset)
        word = words[seed % len(words)]
        scrambled = "".join(sorted(word, reverse=True))
        return Puzzle(
            id=self._id("wordsmith", player_id, level, seed_offset),
            role="Wordsmith",
            kind="linguistic",
            prompt=f"Unscramble this relay term: {scrambled}",
            answer=word,
            level=level,
        )

    def _quant(self, level: int, player_id: str, seed_offset: int) -> Puzzle:
        seed = self._seed(player_id, level, seed_offset)
        values = [12, 13, 12, 14, 13]
        outlier = 30 + seed % 10
        values.insert(seed % len(values), outlier)
        return Puzzle(
            id=self._id("quant", player_id, level, seed_offset),
            role="Quant",
            kind="analysis",
            prompt=f"Find the anomaly in this frequency row: {', '.join(map(str, values))}",
            answer=str(outlier),
            level=level,
            payload={"values": values},
        )

    def _maestro(self, level: int, player_id: str, seed_offset: int) -> Puzzle:
        seed = self._seed(player_id, level, seed_offset)
        start = seed % 4 + 1
        sequence = [start, start + 2, start + 4, start + 6]
        missing = sequence[2]
        visible = [sequence[0], sequence[1], "_", sequence[3]]
        return Puzzle(
            id=self._id("maestro", player_id, level, seed_offset),
            role="Maestro",
            kind="pattern",
            prompt=f"Complete the interval pattern: {' '.join(map(str, visible))}",
            answer=str(missing),
            level=level,
        )

    def _saboteur(self, level: int, player_id: str, seed_offset: int) -> Puzzle:
        seed = self._seed(player_id, level, seed_offset)
        options = ["blur", "shake", "scramble", "dim"]
        choice = options[seed % len(options)]
        return Puzzle(
            id=self._id("saboteur", player_id, level, seed_offset),
            role="Saboteur",
            kind="economy",
            prompt=f"Offense timing drill: name the cheapest visual attack in this set: {choice}, shield, overload.",
            answer=choice,
            level=level,
            payload={"focus": "Use the action center after passing."},
        )

    def _warden(self, level: int, player_id: str, seed_offset: int) -> Puzzle:
        seed = self._seed(player_id, level, seed_offset)
        charge = 2 + seed % 4
        return Puzzle(
            id=self._id("warden", player_id, level, seed_offset),
            role="Warden",
            kind="defense",
            prompt=f"Shield calibration: enter the charge number shown by the defense meter: {charge}",
            answer=str(charge),
            level=level,
            payload={"focus": "Use shield actions after passing."},
        )

    def _id(self, prefix: str, player_id: str, level: int, seed_offset: int) -> str:
        return f"{prefix}-{player_id}-{level}-{seed_offset}"

    def _seed(self, player_id: str, level: int, seed_offset: int) -> int:
        return sum(ord(char) for char in player_id) + level * 31 + seed_offset * 13


def normalize_answer(value: object) -> str:
    return " ".join(str(value).strip().lower().replace("/", " ").split())
