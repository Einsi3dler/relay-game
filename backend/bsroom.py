"""BULLSHIT QUIZ: one room, one non-playing host, up to N rounds.

A room is deliberately not a Match, for the reasons `backend/quizroom.py` and
`backend/duelroom.py` give: no teams, no levels, no currency. The rules live
in `backend/bsquiz.py` and the perks in `backend/bsperks.py`; this file is the
state machine around them and the one place that decides what each viewer
may see.

The phases:

    lobby     players register and write their questions; the host reviews
              and tags every one, blind to its author; nothing is dealt
    draft     tonight's questions are chosen (bsquiz.plan_rounds), the
              roster is frozen and everybody picks one unique perk, in a
              shuffled order they can trade places in (backend/bsdraft.py)
    briefing  a question is up and its panel dealt. Each speaker privately
              learns their role. Bullshitters lock a wager; the truthful
              player confirms "ready" in the same beat (see below)
    open      the discussion on the call, on a clock. The ballot stays shut
              until the clock runs out and any floor perks have run; then
              guessers lock an answer and a wager
    votes     locked. The headcount per option is shown, the truth is not
    reveal    the truth, the roles and every score change, applied once
    waiting   too few players online to deal the next round; the host retries
    final     no playable question left: the standings and the winner(s)

The host presses every step except two: a draft turn running out, and the
discussion clock opening the ballot (docs/BULLSHIT_QUIZ_PERKS.md §0). The
room keeps no clock of its own. Everything time-based takes `now` from the
caller, and the server calls `tick` a couple of times a second to let
clocks and reaction windows run out.

**Perks.** Every player drafts one unique perk before the quiz, and who
holds what is public from then on. Playing it spends it.
Who may play what and when is `_check` below; what perks do to the points is
`bsperks.score`. A perk that lands on somebody else gets a reaction window
(BS_REACTION_SECONDS) for Objection! and Not My Problem before it takes
effect. Secret perks are seen by their owner alone until the round's reveal.

**Leaving never stalls the game** (the owner's rule, docs/BULLSHIT_QUIZ.md
§0). A dropped connection is not leaving: phones drop it whenever the screen
locks. `on_drop` only marks the seat *reconnecting*, which changes nothing
about the game, and the server calls `on_disconnect` (the strict rule below)
if they have not reopened their link within BS_GRACE_SECONDS.

Once a game has started, a player who goes offline keeps their seat and
their score but is *relegated*: a guesser for the rest of the game, never
picked to bluff again, even after they come back. Concretely:

  * Nobody waits on an offline guesser. If they come back mid-round they can
    still guess; if not, they simply cast no vote and spend no wager.
  * A question whose author is offline when its turn comes is skipped and
    stays queued. If they come back it can still be played. When every
    question left belongs to somebody offline, those are dropped for good and
    the game ends.
  * In the briefing, before the discussion opens, a speaker who leaves is
    swapped out: a bullshitter's option goes to another player who may bluff
    (or is removed, if nobody can take it), and an author's question goes
    back in the queue. Any wager locked in that round is refunded, and so is
    any perk the change makes meaningless. Once the discussion is open the
    round carries on and commitments stand.

**`public()` is the security boundary.** Until a round's reveal, no viewer is
told which option is true, who wrote the question, or which speakers are
bullshitters, on any key, nested or otherwise. Each speaker learns only their
own role. The host gets no more than a guesser: they are likely sharing their
screen on the call.

Three rules here look like details and are not:

  * **The truthful player locks too.** In the briefing every speaker's tile
    lights up when they commit. If only bullshitters committed, the one
    speaker who never lit up would be the author. Guess Who shipped that bug
    first (docs/QUIZ_ROLL_CALL.md §2).
  * **A guesser's lock stays dark until the ballot opens.** Only Blind Faith
    can lock earlier, so an early tick would announce it.
  * **A commitment is final, and spends its wager card at once.** Cards are
    single-use, so one taken back and reused would be a second commitment in
    disguise. The only refund is a round that never happened. A hand is a
    list of cards, not a set of numbers: after a draft trade it can hold two
    of the same value.
"""

from __future__ import annotations

import random
import secrets
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable

from backend import bsdraft, bsperks, bsquiz, config
from backend.bsdraft import WagerCard
from backend.bsquiz import BULLSHITTER, GUESSER, TRUTHFUL
from backend.bsroster import Player

PHASES = ("lobby", "draft", "briefing", "open", "votes", "reveal", "waiting", "final")
IN_ROUND = ("briefing", "open", "votes", "reveal")
BEFORE_LOCK = ("briefing", "open")

# What a play is doing. Pending: inside its reaction window. Queued and
# running: floor perks waiting for, or holding, the floor. Live: in effect.
# Done: finished or already applied. Cancelled: objected to.
PENDING, QUEUED, RUNNING, LIVE, DONE, CANCELLED = (
    "pending", "queued", "running", "live", "done", "cancelled")
WAITING = (PENDING, QUEUED)


@dataclass
class Seat:
    player: Player
    connected: bool = False
    reconnecting: bool = False            # socket dropped, inside the grace period
    relegated: bool = False               # went offline mid-game: guesser from now on
    total: int = 0
    ready: bool = False                   # truthful: confirmed in the briefing
    bluff: int | None = None              # bullshitter: their wager
    pick: tuple[str, int] | None = None   # guesser: (option key, wager)
    perk: str | None = None               # drafted, for the whole game
    perk_used: bool = False

    @property
    def id(self) -> str:
        return self.player.id

    @property
    def name(self) -> str:
        return self.player.name

    @property
    def may_bluff(self) -> bool:
        return self.connected and not self.relegated

    def clear_round(self) -> None:
        self.ready = False
        self.bluff = None
        self.pick = None


@dataclass
class Play:
    """One perk played this round. A Counterfeit is stored as the perk it
    copied, with `counterfeit` set."""
    id: int
    perk: str
    by: str
    target: str | None = None
    option: str | None = None
    words: tuple[str, ...] = ()
    counterfeit: bool = False
    status: str = LIVE
    settles_at: float | None = None       # end of the reaction window
    ends_at: float | None = None          # a running floor perk's end
    redirected: bool = False              # by Not My Problem; once only
    ref: int | None = None                # Objection! and Not My Problem: the play they answer
    slips: int = 0                        # Forbidden Words, counted by the host
    seen: str | None = None               # Inside Information: the answer they saw
    swapped: bool = False                 # Hostile Takeover: it happened


class Rejected(Exception):
    """A move the rules do not allow. Carries what to tell the client."""


@dataclass
class BullshitRoom:
    id: str
    code: str = ""                                     # what players type to join
    seats: dict[str, Seat] = field(default_factory=dict)
    phase: str = "lobby"
    n: int = 0                                         # fixed at start
    rounds: int = 0                                    # tonight's questions, fixed at start
    pending: list[str] = field(default_factory=list)   # question ids still to play, in order
    played: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)   # theirs never came back
    current: str | None = None                         # id of the question up
    questions: dict[str, bsquiz.Question] = field(default_factory=dict)  # tonight's, by id
    panel: bsquiz.Panel | None = None
    result: bsquiz.RoundResult | None = None
    history: list[dict[str, Any]] = field(default_factory=list)  # revealed rounds only
    hands: dict[str, list[WagerCard]] = field(default_factory=dict)  # wager cards, by player
    draft: bsdraft.Draft | None = None
    plays: list[Play] = field(default_factory=list)    # perks played this round
    revealed: list[tuple[str, str]] = field(default_factory=list)  # (by, perk) from past rounds
    next_play: int = 1
    ballot: bool = False                               # guessers may lock
    talk_ends: float = 0.0
    openings_end: float = 0.0
    host_connected: bool = False
    rng: random.Random = field(default_factory=lambda: random.Random(secrets.randbits(64)))

    # --- queries ---------------------------------------------------------

    def question(self) -> bsquiz.Question | None:
        return self.questions.get(self.current) if self.current else None

    def role_of(self, player_id: str) -> str | None:
        return self.panel.role_of(player_id) if self.panel else None

    def speakers(self) -> list[Seat]:
        return [self.seats[p] for p in self.panel.speakers] if self.panel else []

    def guessers(self) -> list[Seat]:
        if not self.panel:
            return []
        return [s for s in self.seats.values() if self.panel.role_of(s.id) == GUESSER]

    def awaited_guessers(self) -> list[Seat]:
        """Who the lock waits on: online guessers. An offline one is never
        waited for; if they committed before leaving, their vote still counts."""
        return [s for s in self.guessers() if s.connected]

    def committed(self, seat: Seat) -> bool:
        """Has this seat done what this round asks of them? Deliberately the
        same question for every speaker, truthful or not."""
        role = self.role_of(seat.id)
        if role == TRUTHFUL:
            return seat.ready
        if role == BULLSHITTER:
            return seat.bluff is not None
        if role == GUESSER:
            return seat.pick is not None
        return False

    def shows_committed(self, seat: Seat) -> bool:
        """`committed` as everybody else may see it: a guesser stays dark
        until the ballot opens, or a Blind Faith lock would announce itself."""
        if self.role_of(seat.id) == GUESSER and not self.ballot and self.phase in BEFORE_LOCK:
            return False
        return self.committed(seat)

    def speakers_in(self) -> int:
        return sum(1 for s in self.speakers() if self.committed(s))

    def guessers_in(self) -> int:
        return sum(1 for s in self.awaited_guessers() if self.committed(s))

    def round_number(self) -> int:
        return len(self.played) + (1 if self.phase in IN_ROUND else 0)

    def total_rounds(self) -> int:
        """Tonight's questions, less any dropped because the author never came back."""
        return self.rounds - len(self.dropped)

    def standings(self) -> list[Seat]:
        return sorted(self.seats.values(), key=lambda s: (-s.total, s.name.lower()))

    def hand(self, player_id: str) -> list[WagerCard]:
        return self.hands.get(player_id, [])

    def left(self, player_id: str) -> list[int]:
        """The values of a player's unspent cards, duplicates and all."""
        return sorted(c.value for c in self.hand(player_id) if not c.spent)

    def perk_known_spent(self, seat: Seat) -> bool:
        """Spent, as far as the table knows: a public perk once it is played,
        a secret one only once its round is revealed."""
        if not seat.perk_used:
            return False
        if any(by == seat.id for by, _ in self.revealed):
            return True
        return any(p.by == seat.id and _visible(self, p, None) for p in self.plays)

    def live_plays(self, perk: str | None = None) -> list[Play]:
        """This round's plays still standing (not objected to), optionally of
        one perk."""
        return [p for p in self.plays
                if p.status != CANCELLED and (perk is None or p.perk == perk)]

    def play_of(self, player_id: str) -> Play | None:
        """The perk this player played this round (other than a reaction)."""
        return next((p for p in self.live_plays()
                     if p.by == player_id and p.ref is None), None)

    # --- the view --------------------------------------------------------

    def public(self, viewer_id: str | None, is_host: bool = False,
               now: float | None = None) -> dict[str, Any]:
        """One personalised snapshot. See the module docstring before adding
        anything to it."""
        now = time.monotonic() if now is None else now
        me = self.seats.get(viewer_id or "")
        live_round = self.phase in ("briefing", "open", "votes")
        speaking = set(self.panel.speakers) if self.panel and self.phase in IN_ROUND else set()

        cards = []
        for seat in self.seats.values():
            card = seat.player.card()
            card.update({"connected": seat.connected, "total": seat.total,
                         "reconnecting": seat.reconnecting,
                         "relegated": seat.relegated})
            if self.phase == "lobby":
                card["submitted"] = seat.player.has_question
                card["written"] = min(seat.player.written, seat.player.wanted)
                card["wanted"] = seat.player.wanted
            else:
                card["perk"] = seat.perk
                card["perk_spent"] = self.perk_known_spent(seat)
            if live_round:
                card["speaker"] = seat.id in speaking
                card["committed"] = self.shows_committed(seat)
            if self.phase == "reveal" and self.result:
                line = self.result.lines.get(seat.id)
                card["line"] = _line(line) if line else None
            cards.append(card)

        view: dict[str, Any] = {
            "room_id": self.id,
            "phase": self.phase,
            "you": me.id if me else None,
            "is_host": is_host,
            "players": cards,
            "here": sum(1 for s in self.seats.values() if s.connected),
            "round": self.round_number(),
            "rounds": self.total_rounds(),
            "wager_max": max(0, self.n - 1),
        }

        if self.phase == "lobby":
            view["submitted"] = sum(1 for s in self.seats.values() if s.player.has_question)
            view["registered"] = len(self.seats)
        if self.phase != "lobby":
            # Who holds what is public from the draft on (docs/BULLSHIT_QUIZ_PERKS.md §6).
            view["perk_cards"] = {pid: _card(pid) for pid in bsperks.PERK}
        if self.phase == "draft" and self.draft is not None:
            view["draft"] = bsdraft.public_view(self.draft, now)

        q = self.question()
        if q is not None and self.panel and self.phase in IN_ROUND:
            view["question"] = {
                "prompt": q.prompt,
                "options": [{"key": o.key, "text": o.text, "speaker": o.speaker}
                            for o in self.panel.options],
            }
            view["speakers_in"] = self.speakers_in()
            view["speakers"] = len(self.panel.speakers)
            view["guessers_in"] = sum(1 for s in self.awaited_guessers()
                                      if self.shows_committed(s))
            view["guessers"] = len(self.awaited_guessers())
            if self.phase == "open":
                view["talk"] = {
                    "left": max(0.0, self.talk_ends - now),
                    "openings_left": max(0.0, self.openings_end - now),
                    "ballot": self.ballot,
                }
            if self.phase in ("votes", "reveal") and self.result:
                view["counts"] = dict(self.result.counts)
                view["tied"] = bsquiz.tied_top(self.result.counts)
            if self.phase == "reveal" and self.result:
                # Earned: the round is scored, and this is the point of it.
                view["truth"] = {
                    "key": self.panel.correct_key,
                    "author": self.panel.author,
                    "explanation": q.explanation,
                    "bullshitters": list(self.panel.bullshitters),
                    "winner": self.result.winner,
                }
            view["perks"] = [v for v in (_play_view(self, p, viewer_id, now)
                                         for p in self.plays) if v is not None]

        if is_host and self.phase != "lobby":
            # Host only, by the owner's call: players see each round's reveal
            # as it happens but get no running record of who bluffed when.
            # Finished rounds only, each recorded at its reveal.
            view["history"] = self.history
            # The one secret perk the host must see to referee it. The page
            # keeps it in a folded panel, away from a shared screen.
            view["host_secrets"] = [_play_view(self, p, p.by, now)
                                    for p in self.live_plays("false_witness")
                                    if self.phase in BEFORE_LOCK]

        if me is not None:
            view["your_total"] = me.total
            view["cards"] = [{"id": c.id, "value": c.value, "spent": c.spent}
                             for c in sorted(self.hand(me.id), key=lambda c: (c.value, c.id))]
            view["relegated"] = me.relegated
            if self.phase == "draft" and self.draft is not None:
                view["draft_offers"] = bsdraft.offers_for(self.draft, me.id)
            role = self.role_of(me.id) if self.phase in IN_ROUND else None
            if role is not None:
                view["role"] = role
                if role in (TRUTHFUL, BULLSHITTER):
                    view["your_option"] = self.panel.option_of(me.id).key
                if role == TRUTHFUL and q is not None:
                    view["your_explanation"] = q.explanation
                view["committed"] = self.committed(me)
                view["your_wager"] = me.bluff if role == BULLSHITTER else (
                    me.pick[1] if me.pick else None)
                if role == GUESSER and me.pick:
                    view["your_pick"] = me.pick[0]
                if role == GUESSER and self.phase in BEFORE_LOCK and me.pick is None:
                    forced = _forced_wager(self, me)
                    if forced is not None:
                        view["forced_wager"] = forced
                    if self.play_of(me.id) and self.play_of(me.id).perk == "read_the_room":
                        waiting = _read_the_room_waiting(self, me)
                        view["read_the_room"] = {"waiting": waiting}
                        if self.ballot and waiting == 0:
                            view["read_the_room"]["counts"] = _counts_without_readers(self)
            if self.phase not in ("lobby", "draft", "final") and me.perk is not None:
                view["perk"] = _my_perk(self, me, now)

        if self.phase == "final":
            totals = {s.id: s.total for s in self.seats.values()}
            view["standings"] = [{"id": s.id, "name": s.name, "total": s.total}
                                 for s in self.standings()]
            view["winners"] = bsquiz.winners(totals)
            view["dropped"] = len(self.dropped)
        return view


def _line(line: bsquiz.Line) -> dict[str, Any]:
    return {"role": line.role, "option": line.option, "wager": line.wager,
            "fooled": line.fooled, "won": line.won, "delta": line.delta,
            "stake": line.stake, "parts": [list(p) for p in line.parts]}


# --- perks in the view ----------------------------------------------------

def _card(perk_id: str) -> dict[str, Any]:
    perk = bsperks.PERK[perk_id]
    return {"id": perk.id, "name": perk.name, "text": perk.text,
            "family": perk.family, "public": perk.public}


def _visible(room: BullshitRoom, play: Play, viewer: str | None) -> bool:
    """Who may see a play before the reveal. Public perks: everybody. Secret
    ones: their owner. False Witness: also its recruit. Hostile Takeover:
    nobody else until the reveal. A reaction is seen by whoever may see the
    play it answers. At the reveal, everything."""
    if room.phase == "reveal" or play.by == viewer:
        return True
    if play.ref is not None:
        answered = next((p for p in room.plays if p.id == play.ref), None)
        return answered is not None and _visible(room, answered, viewer)
    if play.perk == "false_witness":
        return viewer is not None and viewer == play.target
    return bsperks.PERK[play.perk].public


def _play_view(room: BullshitRoom, play: Play, viewer: str | None,
               now: float) -> dict[str, Any] | None:
    if not _visible(room, play, viewer):
        return None
    view: dict[str, Any] = {
        "id": play.id, "perk": play.perk, "name": bsperks.PERK[play.perk].name,
        "family": bsperks.PERK[play.perk].family,
        "by": play.by, "target": play.target, "status": play.status,
        "counterfeit": play.counterfeit, "redirected": play.redirected,
    }
    if play.option is not None:
        view["option"] = play.option
    if play.words:
        view["words"] = list(play.words)
        view["slips"] = play.slips
    if play.ref is not None:
        view["ref"] = play.ref
    if play.status == PENDING and play.settles_at is not None:
        view["lands_in"] = max(0.0, play.settles_at - now)
    if play.status == RUNNING and play.ends_at is not None:
        view["ends_in"] = max(0.0, play.ends_at - now)
    if play.perk == "the_audit" and play.status in (LIVE, DONE):
        pick = room.seats[play.target].pick if play.target in room.seats else None
        if pick:
            view["exposed"] = {"option": pick[0], "wager": pick[1]}
    if play.perk == "inside_information" and play.seen is not None:
        view["seen"] = play.seen
    if play.perk == "false_witness" and room.panel and room.panel.option_of(play.by):
        view["option"] = room.panel.option_of(play.by).key
    if play.perk == "hostile_takeover":
        view["swapped"] = play.swapped
    return view


def _my_perk(room: BullshitRoom, seat: Seat, now: float) -> dict[str, Any]:
    """Your perk, and whether, on whom and with what you could play it right
    now."""
    view = {**_card(seat.perk), "used": seat.perk_used}
    if seat.perk_used or room.phase not in IN_ROUND:
        return view
    if seat.perk == "counterfeit":
        copies = [{**_card(c), **_affordance(room, seat, c, now)}
                  for c in _copyable(room, seat)]
        view["copies"] = copies
        view["ready"] = any(c["ready"] for c in copies)
        view["why"] = None if copies else "Nothing has been revealed to copy yet."
    else:
        view.update(_affordance(room, seat, seat.perk, now))
    return view


def _affordance(room: BullshitRoom, seat: Seat, perk: str, now: float) -> dict[str, Any]:
    perk_def = bsperks.PERK[perk]
    if perk in ("objection", "not_my_problem"):
        reactions = _reactions(room, seat, perk, now)
        return {"needs": perk_def.needs, "ready": bool(reactions), "reactions": reactions,
                "why": None if reactions else (
                    "Nothing to object to right now." if perk == "objection"
                    else "Nothing has landed on you.")}
    problem, targets, options = _check(room, seat, perk, now)
    return {"needs": perk_def.needs, "ready": problem is None, "why": problem,
            "targets": targets, "options": options}


def _forced_wager(room: BullshitRoom, seat: Seat) -> int | None:
    """A blackmailed guesser's wager: their highest number left."""
    if not any(p.target == seat.id for p in room.live_plays("blackmail")):
        return None
    left = room.left(seat.id)
    return max(left) if left else None


def _readers(room: BullshitRoom) -> set[str]:
    return {p.by for p in room.live_plays("read_the_room")}


def _read_the_room_waiting(room: BullshitRoom, seat: Seat) -> int:
    """How many other guessers a Read the Room player is still waiting on.
    Read the Room players never wait on each other."""
    readers = _readers(room)
    return sum(1 for s in room.awaited_guessers()
               if s.id != seat.id and s.id not in readers and s.pick is None)


def _counts_without_readers(room: BullshitRoom) -> dict[str, int]:
    readers = _readers(room)
    guesses = {s.id: s.pick for s in room.guessers()
               if s.pick is not None and s.id not in readers}
    return bsquiz.vote_counts(room.panel, guesses)


# --- who may play what, when ----------------------------------------------

def _locked(room: BullshitRoom, seat: Seat) -> bool:
    role = room.role_of(seat.id)
    if role == GUESSER:
        return seat.pick is not None
    if role == BULLSHITTER:
        return seat.bluff is not None
    return True


def _talking(room: BullshitRoom, now: float) -> bool:
    return room.phase == "open" and not room.ballot and now < room.talk_ends


def _in_pact(room: BullshitRoom, player_id: str) -> bool:
    return any(player_id in (p.by, p.target) for p in room.live_plays("blood_pact"))


def _wrong_guessers(room: BullshitRoom) -> list[str]:
    if room.result is None:
        return []
    return [p for p, line in room.result.lines.items()
            if line.role == GUESSER and line.option is not None and not line.won]


def _check(room: BullshitRoom, seat: Seat, perk: str,
           now: float) -> tuple[str | None, list[str], list[str]]:
    """Why `seat` cannot play `perk` right now (None if it can), who it may
    aim it at, and which options it may name. docs/BULLSHIT_QUIZ_PERKS.md §4
    is the source for every line of this."""
    if room.phase not in IN_ROUND or room.panel is None:
        return "Perks are played during a round.", [], []
    p = bsperks.PERK[perk]
    me, phase, role = seat.id, room.phase, room.role_of(seat.id)
    if p.who == bsperks.WHO_GUESSER and role != GUESSER:
        return "Only a guesser can play this.", [], []
    if p.who == bsperks.WHO_SPEAKER and role == GUESSER:
        return "Only a speaker can play this.", [], []
    if p.who == bsperks.WHO_WAGER and role == TRUTHFUL:
        return "You have no wager this round.", [], []

    guessers = [s.id for s in room.guessers()]
    others = [g for g in guessers if g != me]
    speakers = [s for s in room.panel.speakers if s != me]
    keys = [o.key for o in room.panel.options]
    before_lock = phase in BEFORE_LOCK
    unlocked = not _locked(room, seat)
    talking = _talking(room, now)
    targets: list[str] = []
    options: list[str] = []

    def need(ok: bool, why: str) -> str | None:
        return None if ok else why

    problem: str | None = None
    if perk in ("insurance_fraud", "double_or_nothing"):
        problem = need(before_lock and unlocked, "Play it before you lock your wager.")
    elif perk == "backup_plan":
        problem = (need(len(keys) >= 3, "Needs a round with three or more options.")
                   or need(before_lock and unlocked, "Play it before you lock your answer."))
        options = keys
    elif perk == "blind_faith":
        problem = (need(len(keys) >= 3, "Needs a round with three or more options.")
                   or need(phase == "briefing" and unlocked,
                           "Blind Faith is played before the talking starts."))
        options = keys
    elif perk == "damage_control":
        line = room.result.lines.get(me) if room.result else None
        problem = (need(phase == "reveal", "Play it after the reveal.")
                   or need(line is not None and line.stake < 0,
                           "You didn't lose a wager this round."))
    elif perk in ("ride_or_die", "terrible_judgement"):
        problem, targets = need(before_lock, "Voting has closed."), others
    elif perk == "lone_wolf":
        problem = (need(len(guessers) >= 3, "Needs at least three guessers this round.")
                   or need(before_lock, "Voting has closed."))
    elif perk == "call_bullshit":
        problem, targets = need(before_lock, "Voting has closed."), list(room.panel.speakers)
    elif perk == "read_the_room":
        problem = need(phase == "briefing" and unlocked,
                       "Read the Room is declared before the talking starts.")
    elif perk == "inside_information":
        problem = (need(phase == "open" and room.ballot, "Wait for voting to open.")
                   or need(unlocked, "You've already locked in."))
        targets = [g for g in others if room.seats[g].pick is not None]
    elif perk == "the_audit":
        problem = need(phase == "open" and room.ballot, "Wait for voting to open.")
        targets = [g for g in others if room.seats[g].pick is not None]
    elif perk in ("silver_tongue", "victory_lap", "honourable_mention"):
        problem = need(before_lock, "Voting has closed.")
    elif perk in ("target_audience", "false_witness"):
        problem = need(phase == "briefing", "Play it before the talking starts.")
        targets = guessers
    elif perk == "hot_seat":
        problem, targets = need(talking, "Only while the talking clock is running."), speakers
    elif perk == "mute_button":
        problem = (need(talking, "Only while the talking clock is running.")
                   or need(now >= room.openings_end, "Wait for the openings to finish."))
        targets = speakers
    elif perk == "last_word":
        problem = need(talking, "Only while the talking clock is running.")
    elif perk == "forbidden_words":
        problem = need(phase == "briefing" or talking, "Only before the talking stops.")
        banned = {q.target for q in room.live_plays("forbidden_words")}
        targets = [s for s in speakers if s not in banned]
    elif perk == "blackmail":
        problem = need(before_lock, "Voting has closed.")
        squeezed = {q.target for q in room.live_plays("blackmail")}
        targets = [g for g in guessers if g != me and room.seats[g].pick is None
                   and g not in squeezed]
    elif perk == "blood_pact":
        problem = (need(before_lock and unlocked, "Play it before you lock in.")
                   or need(not _in_pact(room, me), "You're already in a pact."))
        targets = [g for g in others if room.seats[g].pick is None and not _in_pact(room, g)]
    elif perk == "pickpocket":
        problem = need(phase == "reveal", "Play it after the reveal.")
        targets = [g for g in _wrong_guessers(room) if g != me]
    elif perk == "kingmaker":
        tied = bsquiz.tied_top(room.result.counts) if room.result else []
        problem = (need(phase == "votes", "Play it once the votes are counted.")
                   or need(bool(tied), "Only when the biggest crowd is a tie.")
                   or need(not room.live_plays("kingmaker"), "The tie is already settled."))
        options = tied
    elif perk == "doppelganger":
        problem = need(before_lock, "Voting has closed.")
        targets = [s for s in room.seats if s != me]
    elif perk == "hostile_takeover":
        problem = (need(before_lock and unlocked, "Play it before you lock in.")
                   or need(bool(room.pending), "Not in the last round."))
        targets = [s for s in room.seats if s != me]
    else:
        problem = "That perk is played another way."

    if problem is None and p.needs in (bsperks.NEEDS_GUESSER, bsperks.NEEDS_SPEAKER,
                                       bsperks.NEEDS_PLAYER, bsperks.NEEDS_WORDS) and not targets:
        problem = "Nobody you could pick right now."
    if problem is None and p.needs in (bsperks.NEEDS_OPTION, bsperks.NEEDS_ANSWER) and not options:
        problem = "No option you could pick right now."
    return problem, targets, options


def _copyable(room: BullshitRoom, seat: Seat) -> list[str]:
    """Perks other players have used and revealed this game: past rounds at
    their reveal, this round's public ones the moment they were played."""
    seen = [perk for by, perk in room.revealed if by != seat.id]
    seen += [p.perk for p in room.plays if p.by != seat.id and _visible(room, p, None)]
    return [perk for perk in dict.fromkeys(seen) if perk != "counterfeit"]


def _redirect_targets(room: BullshitRoom, play: Play) -> list[str]:
    """Where Not My Problem may send `play`: anybody that perk could have hit
    in the first place, which can include whoever played it."""
    cur, by = play.target, play.by
    guessers = [s.id for s in room.guessers()]
    speakers = list(room.panel.speakers) if room.panel else []
    if play.perk in ("hot_seat", "mute_button"):
        return [s for s in speakers if s != cur and not (play.perk == "hot_seat" and s == by)]
    if play.perk == "forbidden_words":
        banned = {q.target for q in room.live_plays("forbidden_words")}
        return [s for s in speakers if s != cur and s not in banned
                and bsperks.words_problem(list(play.words),
                                          room.panel.option_of(s).text) is None]
    if play.perk == "blackmail":
        squeezed = {q.target for q in room.live_plays("blackmail")}
        return [g for g in guessers if g != cur and room.seats[g].pick is None
                and g not in squeezed]
    if play.perk == "blood_pact":
        return [g for g in guessers if g not in (cur, by) and room.seats[g].pick is None
                and not _in_pact(room, g)]
    if play.perk == "the_audit":
        return [g for g in guessers if g != cur and room.seats[g].pick is not None]
    if play.perk == "pickpocket":
        return [g for g in _wrong_guessers(room) if g != cur]
    if play.perk == "false_witness":
        return [g for g in guessers if g != cur]
    if play.perk == "hostile_takeover":
        return [s for s in room.seats if s not in (cur, by)]
    return []


def _reactions(room: BullshitRoom, seat: Seat, perk: str, now: float) -> list[dict[str, Any]]:
    """What an Objection! or Not My Problem holder could answer right now."""
    out = []
    for play in room.plays:
        if play.status not in WAITING or play.by == seat.id:
            continue
        if perk == "objection" and play.perk in bsperks.FLOOR:
            out.append({"play": play.id, "name": bsperks.PERK[play.perk].name,
                        "by": play.by, "target": play.target})
        elif (perk == "not_my_problem" and play.target == seat.id
              and play.perk in bsperks.REDIRECTABLE and not play.redirected):
            targets = _redirect_targets(room, play)
            if targets:
                out.append({"play": play.id, "name": bsperks.PERK[play.perk].name,
                            "by": play.by, "targets": targets})
    return out


# --- lifecycle ------------------------------------------------------------

# Read off a phone in a group chat and typed on another: no 0/O or 1/I.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(config.BS_CODE_LENGTH))


def create_room(players: list[Player], rng: random.Random | None = None,
                code: str | None = None) -> BullshitRoom:
    room = BullshitRoom(id=secrets.token_hex(4), code=code or new_code())
    if rng is not None:
        room.rng = rng
    refresh(room, players)
    return room


def refresh(room: BullshitRoom, players: list[Player]) -> None:
    """Pick up who has joined (or been removed) and new questions. Lobby only:
    once the game starts the roster and N are fixed (handoff §3)."""
    if room.phase != "lobby":
        return
    present = {person.id for person in players}
    for gone in [pid for pid in room.seats if pid not in present]:
        del room.seats[gone]
    for person in players:
        if not person.registered:
            continue
        seat = room.seats.get(person.id)
        if seat is None:
            room.seats[person.id] = Seat(player=person)
        else:
            seat.player = person


def on_connect(room: BullshitRoom, player_id: str) -> None:
    """Back online. Relegation stands: they guess for the rest of the game.
    Their queued questions, if any, become playable again."""
    seat = room.seats.get(player_id)
    if seat is not None:
        seat.connected = True
        seat.reconnecting = False


def on_drop(room: BullshitRoom, player_id: str) -> None:
    """Their socket closed. Still present as far as the game is concerned:
    still waited on, still eligible to bluff. Display only."""
    seat = room.seats.get(player_id)
    if seat is not None and seat.connected:
        seat.reconnecting = True


def on_disconnect(room: BullshitRoom, player_id: str) -> None:
    """Gone: the grace period ran out (or, in tests, called directly)."""
    seat = room.seats.get(player_id)
    if seat is None:
        return
    seat.connected = False
    seat.reconnecting = False
    if room.phase == "lobby":
        return
    seat.relegated = True
    if room.phase == "briefing" and room.panel and seat.id in room.panel.speakers:
        if seat.id == room.panel.author:
            _defer_current(room)
        else:
            _replace_bullshitter(room, seat)


def can_submit(room: BullshitRoom) -> None:
    if room.phase != "lobby":
        raise Rejected("The game has started. Questions are locked.")


def start(room: BullshitRoom, questions: dict[str, bsquiz.Question], rounds: int) -> None:
    """Freeze the roster, fix N, choose tonight's questions and their order
    (up to `rounds` of them, bsquiz.plan_rounds), deal everybody their wager
    cards, and open the perk draft. The first round is dealt when the host
    starts the quiz after it (`begin_quiz`).

    Every seat must have written all their questions and the host must have
    tagged every one. Anybody seated but offline at this moment starts
    relegated, and their questions wait for them like any other absent
    author's.
    """
    if room.phase != "lobby":
        raise Rejected("The game has already started.")
    if len(room.seats) < config.BS_MIN_PLAYERS:
        raise Rejected(f"Wait for at least {config.BS_MIN_PLAYERS} players to sign in.")
    bank = [q for q in questions.values() if q.author in room.seats]
    missing = [s.name for s in room.seats.values()
               if sum(1 for q in bank if q.author == s.id) < s.player.wanted]
    if missing:
        raise Rejected("Still waiting on questions from " + ", ".join(sorted(missing)) + ".")
    untagged = sum(1 for q in bank if q.tag not in config.BS_TAGS)
    if untagged:
        raise Rejected(f"Review every question first: {untagged} still "
                       f"need{'s' if untagged == 1 else ''} a tag.")
    if rounds < 1:
        raise Rejected("Set how many rounds tonight has.")
    if not any(q.tag in config.BS_TAG_WEIGHTS for q in bank):
        raise Rejected("Every question is marked Leave out. Tag at least one to play it.")
    if sum(1 for s in room.seats.values() if s.connected) < config.BS_MIN_PLAYERS:
        raise Rejected(f"At least {config.BS_MIN_PLAYERS} players need to be online to start.")
    if len(room.seats) > len(bsperks.PERKS):
        raise Rejected(f"The perk draft has {len(bsperks.PERKS)} perks, so a game takes "
                       f"at most {len(bsperks.PERKS)} players.")

    room.n = len(room.seats)
    plan = bsquiz.plan_rounds(bank, rounds, room.rng)
    room.questions = {q.id: q for q in plan}
    room.pending = [q.id for q in plan]
    room.rounds = len(plan)
    room.played = []
    room.dropped = []
    room.history = []
    room.revealed = []
    most = max(1 + min(len(q.decoys), bsquiz.max_bullshitters(room.n)) for q in plan)
    room.hands = bsdraft.deal_hands(list(room.seats), bsquiz.wager_hand(room.n, room.rounds))
    room.draft = bsdraft.new_draft(list(room.seats), room.hands,
                                   bsperks.draft_pool(room.n, most), room.rng)
    for seat in room.seats.values():
        seat.total = 0
        seat.relegated = not seat.connected
        seat.perk = None
        seat.perk_used = False
        seat.clear_round()
    room.phase = "draft"


# --- the draft ------------------------------------------------------------

def _drafting(room: BullshitRoom) -> bsdraft.Draft:
    if room.phase != "draft" or room.draft is None:
        raise Rejected("The perk draft is over.")
    return room.draft


def _sync_picks(room: BullshitRoom) -> None:
    for player_id, perk in room.draft.picks.items():
        room.seats[player_id].perk = perk


def draft_offer(room: BullshitRoom, player_id: str, to: object, give: object,
                take: object) -> None:
    _seat(room, player_id)
    bsdraft.propose(_drafting(room), player_id, to, give, take)


def draft_respond(room: BullshitRoom, player_id: str, offer: object, answer: object) -> None:
    _seat(room, player_id)
    bsdraft.respond(_drafting(room), player_id, offer, answer)


def draft_go(room: BullshitRoom, now: float | None = None) -> None:
    """The host starts the picks: the first player's clock starts."""
    now = time.monotonic() if now is None else now
    bsdraft.start_picking(_drafting(room), now)


def draft_pick(room: BullshitRoom, player_id: str, perk: object, turn: object,
               now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    _seat(room, player_id)
    bsdraft.pick(_drafting(room), player_id, perk, turn, now)
    _sync_picks(room)


def begin_quiz(room: BullshitRoom) -> None:
    """The host starts the quiz once every perk is drafted."""
    draft = _drafting(room)
    if draft.stage != bsdraft.COMPLETE:
        raise Rejected("Finish the draft first.")
    _sync_picks(room)
    _deal_next(room)


# --- dealing --------------------------------------------------------------

def _dealable_size(room: BullshitRoom, q: bsquiz.Question) -> int:
    candidates = [s for s in room.seats.values() if s.id != q.author and s.may_bluff]
    online_others = sum(1 for s in room.seats.values() if s.id != q.author and s.connected)
    return bsquiz.panel_size(len(q.decoys), len(candidates), online_others)


def _deal_next(room: BullshitRoom) -> None:
    """Deal the first queued question whose author is online.

    If every queued author is offline, the game is over and those questions
    are dropped. If some are online but too few players are, the room waits
    for the host to try again.
    """
    for seat in room.seats.values():
        seat.clear_round()
    room.panel = None
    room.result = None
    room.current = None
    room.plays = []
    room.ballot = False
    room.talk_ends = room.openings_end = 0.0

    playable = [qid for qid in room.pending
                if room.seats[room.questions[qid].author].connected]
    if not playable:
        room.dropped.extend(room.pending)
        room.pending = []
        room.phase = "final"
        return

    for qid in playable:
        q = room.questions[qid]
        b = _dealable_size(room, q)
        if b >= 1:
            candidates = [s.id for s in room.seats.values() if s.id != q.author and s.may_bluff]
            room.panel = bsquiz.deal_panel(q, candidates, b, room.rng)
            room.pending.remove(qid)
            room.current = qid
            room.phase = "briefing"
            return
    room.phase = "waiting"


def _refund_plays(room: BullshitRoom, which: Callable[[Play], bool]) -> None:
    """Hand back the perks of every play `which` picks, as if never played.
    A reaction to a refunded play is refunded with it."""
    gone = {p.id for p in room.plays if which(p)}
    gone |= {p.id for p in room.plays if p.ref in gone}
    for play in room.plays:
        if play.id in gone:
            room.seats[play.by].perk_used = False
            if play.perk == "blind_faith":
                _unpick(room, room.seats[play.by])
    room.plays = [p for p in room.plays if p.id not in gone]


def _unpick(room: BullshitRoom, seat: Seat) -> None:
    if seat.pick is not None:
        _unspend(room, seat, seat.pick[1])
        seat.pick = None


def _refund_round(room: BullshitRoom) -> None:
    """A round that will not happen hands back every wager locked in it, and
    every perk played in it."""
    _refund_plays(room, lambda play: True)
    for seat in room.seats.values():
        if seat.bluff is not None:
            _unspend(room, seat, seat.bluff)
        _unpick(room, seat)
        seat.clear_round()


def _defer_current(room: BullshitRoom) -> None:
    """The author left during the briefing: their question goes back to the
    front of the queue, where it waits for them, and the next one is dealt."""
    _refund_round(room)
    room.pending.insert(0, room.current)
    _deal_next(room)


def _replace_bullshitter(room: BullshitRoom, gone: Seat) -> None:
    """A bullshitter left during the briefing. Hand their option to somebody
    who may bluff; failing that, remove the option; failing that (it was the
    only one), the round cannot run and the question is deferred.

    Perks by or aimed at anybody whose role changes are handed back. So is
    every Blind Faith lock and Backup Plan if an option disappears, because
    the options are lettered afresh."""
    if gone.bluff is not None:
        _unspend(room, gone, gone.bluff)
    gone.clear_round()

    speakers = set(room.panel.speakers)
    takers = [s.id for s in room.seats.values() if s.may_bluff and s.id not in speakers]
    online_guessers = sum(1 for s in room.seats.values()
                          if s.connected and s.id not in speakers)
    entries = [(o.text, o.speaker, o.correct) for o in room.panel.options]

    if takers and online_guessers - 1 >= 1:
        taker = room.rng.choice(takers)
        _refund_plays(room, lambda p: {p.by, p.target} & {gone.id, taker})
        _unpick(room, room.seats[taker])
        entries = [(t, taker if who == gone.id else who, c) for t, who, c in entries]
    elif len(room.panel.bullshitters) > 1:
        _refund_plays(room, lambda p: gone.id in (p.by, p.target) or p.option is not None
                      or p.perk == "blind_faith")
        entries = [e for e in entries if e[1] != gone.id]
    else:
        _defer_current(room)
        return
    room.panel = bsquiz.panel_from(room.panel.author, entries)


# --- commitments ----------------------------------------------------------

def _seat(room: BullshitRoom, player_id: str) -> Seat:
    seat = room.seats.get(player_id)
    if seat is None:
        raise Rejected("You are not in this game.")
    return seat


def _spend(room: BullshitRoom, seat: Seat, wager: object) -> int:
    """Spend one of the seat's unspent cards of this value. Two cards of the
    same value in one hand are interchangeable, so any will do."""
    problem = bsquiz.wager_problem(wager, room.left(seat.id))
    if problem:
        raise Rejected(problem)
    card = next(c for c in room.hand(seat.id) if c.value == wager and not c.spent)
    card.spent = True
    return wager                    # type: ignore[return-value]


def _unspend(room: BullshitRoom, seat: Seat, value: int) -> None:
    card = next((c for c in room.hand(seat.id) if c.value == value and c.spent), None)
    if card is not None:
        card.spent = False


def ready(room: BullshitRoom, player_id: str) -> None:
    """The truthful player's commitment in the briefing. No wager."""
    seat = _seat(room, player_id)
    if room.phase != "briefing":
        raise Rejected("Nothing to confirm right now.")
    if room.role_of(seat.id) != TRUTHFUL:
        raise Rejected("Only the truthful player confirms. Lock a wager instead.")
    if seat.ready:
        raise Rejected("You have already confirmed.")
    seat.ready = True


def bluff(room: BullshitRoom, player_id: str, wager: object) -> None:
    """A bullshitter's wager on winning the crowd, before the discussion."""
    seat = _seat(room, player_id)
    if room.phase != "briefing":
        raise Rejected("Bullshitters wager before the discussion opens.")
    if room.role_of(seat.id) != BULLSHITTER:
        raise Rejected("You are not bluffing this round.")
    if seat.bluff is not None:
        raise Rejected("Your wager is locked.")
    seat.bluff = _spend(room, seat, wager)


def guess(room: BullshitRoom, player_id: str, key: object, wager: object) -> None:
    """A guesser's answer and wager, once the ballot opens."""
    seat = _seat(room, player_id)
    if room.phase != "open":
        raise Rejected("Guesses open once the discussion starts.")
    if room.role_of(seat.id) != GUESSER:
        raise Rejected("Speakers do not guess.")
    if seat.pick is not None:
        raise Rejected("Your answer is locked.")
    if not room.ballot:
        raise Rejected("Voting opens when the talking stops.")
    if not isinstance(key, str) or room.panel.option(key) is None:
        raise Rejected("Pick one of the options.")
    mine = room.play_of(seat.id)
    if mine and mine.perk == "read_the_room" and _read_the_room_waiting(room, seat):
        raise Rejected("Read the Room: wait until everybody else has locked in.")
    if mine and mine.perk == "backup_plan" and mine.option == key:
        raise Rejected("That's your backup. Pick a different main answer.")
    forced = _forced_wager(room, seat)
    seat.pick = (key, _spend(room, seat, forced if forced is not None else wager))


# --- perks ----------------------------------------------------------------

def play_perk(room: BullshitRoom, player_id: str, fields: dict[str, Any],
              now: float | None = None) -> None:
    """Play your perk. `fields` carries whatever it needs: `target`, `option`,
    `wager` (Blind Faith), `words` (Forbidden Words), `play` (Objection! and
    Not My Problem) and `copy` (Counterfeit)."""
    now = time.monotonic() if now is None else now
    seat = _seat(room, player_id)
    if seat.perk is None:
        raise Rejected("Pick your perk first.")
    if seat.perk_used:
        raise Rejected("You've already used your perk.")
    perk, counterfeit = seat.perk, False
    if perk == "counterfeit":
        copy = fields.get("copy")
        if not isinstance(copy, str) or copy not in _copyable(room, seat):
            raise Rejected("Copy a perk somebody else has used and revealed.")
        perk, counterfeit = copy, True

    if perk in ("objection", "not_my_problem"):
        _react(room, seat, perk, fields, counterfeit, now)
        return

    problem, targets, options = _check(room, seat, perk, now)
    if problem:
        raise Rejected(problem)
    needs = bsperks.PERK[perk].needs
    target = fields.get("target")
    option = fields.get("option")
    if needs in (bsperks.NEEDS_GUESSER, bsperks.NEEDS_SPEAKER, bsperks.NEEDS_PLAYER,
                 bsperks.NEEDS_WORDS) and target not in targets:
        raise Rejected("Pick somebody from the list.")
    if needs in (bsperks.NEEDS_OPTION, bsperks.NEEDS_ANSWER) and option not in options:
        raise Rejected("Pick one of the options.")
    play = Play(id=room.next_play, perk=perk, by=seat.id, counterfeit=counterfeit,
                target=target if isinstance(target, str) else None,
                option=option if isinstance(option, str) else None)

    if perk == "forbidden_words":
        words = fields.get("words")
        problem = bsperks.words_problem(words, room.panel.option_of(target).text)
        if problem:
            raise Rejected(problem)
        play.words = bsperks.clean_words(words)
    if perk == "blind_faith":
        forced = _forced_wager(room, seat)
        seat.pick = (option, _spend(room, seat, forced if forced is not None
                                    else fields.get("wager")))
    if perk == "inside_information":
        play.seen = room.seats[target].pick[0]

    if perk in bsperks.REDIRECTABLE and perk != "hostile_takeover":
        play.status, play.settles_at = PENDING, now + config.BS_REACTION_SECONDS
    elif perk == "last_word":
        play.status = QUEUED
    room.plays.append(play)
    room.next_play += 1
    seat.perk_used = True

    if perk == "kingmaker":
        room.result = _score(room)
    if perk == "damage_control":
        play.status = DONE
        _bump(room, seat.id, perk, bsperks.damage_control_points(room.result.lines[seat.id].stake))


def _react(room: BullshitRoom, seat: Seat, perk: str, fields: dict[str, Any],
           counterfeit: bool, now: float) -> None:
    """Objection! and Not My Problem: answers to somebody else's play."""
    if room.phase not in IN_ROUND:
        raise Rejected("Perks are played during a round.")
    ref = fields.get("play")
    answered = next((p for p in room.plays if p.id == ref), None)
    if answered is None or answered.status not in WAITING or answered.by == seat.id:
        raise Rejected("That perk can't be answered any more.")
    target = None
    if perk == "objection":
        if answered.perk not in bsperks.FLOOR:
            raise Rejected("Objection! only cancels speaking perks.")
        answered.status = CANCELLED
    else:
        if answered.target != seat.id or answered.perk not in bsperks.REDIRECTABLE:
            raise Rejected("Only a perk aimed at you can be sent on.")
        if answered.redirected:
            raise Rejected("That one has already been sent on once.")
        target = fields.get("target")
        if target not in _redirect_targets(room, answered):
            raise Rejected("Pick somebody it could have hit.")
        answered.target, answered.redirected = target, True
        if answered.status == PENDING:
            answered.settles_at = now + config.BS_REACTION_SECONDS
    room.plays.append(Play(id=room.next_play, perk=perk, by=seat.id, target=target,
                           ref=answered.id, counterfeit=counterfeit, status=DONE))
    room.next_play += 1
    seat.perk_used = True


def _bump(room: BullshitRoom, player_id: str, perk: str, points: int) -> None:
    """A reveal-time perk's points: onto the total at once, and onto the
    round's line and history so the reveal shows them."""
    room.seats[player_id].total += points
    lines = room.result.lines
    line = lines.get(player_id) or bsquiz.Line(
        player=player_id, role=room.role_of(player_id) or GUESSER, option=None, wager=None)
    lines[player_id] = replace(line, delta=line.delta + points,
                               parts=(*line.parts, (bsperks.PERK[perk].name, points)))
    if room.history:
        room.history[-1]["deltas"][player_id] = lines[player_id].delta


def _land(room: BullshitRoom, play: Play) -> None:
    """A reaction window ran out: the perk takes effect."""
    if play.perk in bsperks.TIMED_FLOOR:
        play.status = QUEUED
    elif play.perk == "pickpocket":
        points = config.BS_PERK_POINTS["pickpocket"]
        _bump(room, play.target, "pickpocket", -points)
        _bump(room, play.by, "pickpocket", points)
        play.status = DONE
    elif play.perk == "hostile_takeover":
        a, b = room.seats[play.by], room.seats[play.target]
        a.total, b.total = b.total, a.total
        play.status, play.swapped = DONE, True
    else:
        play.status = LIVE
    play.settles_at = None


def tick(room: BullshitRoom, now: float | None = None) -> bool:
    """Let the clocks run: reaction windows close, floor perks take and give
    up the floor, and the ballot opens when the talking is over. True if
    anything changed, so the server knows to broadcast."""
    now = time.monotonic() if now is None else now
    changed = False
    if room.phase == "draft" and room.draft is not None:
        if bsdraft.tick(room.draft, now, room.rng):
            _sync_picks(room)
            return True
        return False
    for play in room.plays:
        if play.status == PENDING and play.settles_at is not None and now >= play.settles_at:
            _land(room, play)
            changed = True
    if room.phase != "open":
        return changed

    running = next((p for p in room.plays if p.status == RUNNING), None)
    if running is not None and now >= (running.ends_at or 0):
        running.status, running = DONE, None
        changed = True
    if running is None:
        up = next((p for p in room.plays if p.status == QUEUED and p.perk in bsperks.TIMED_FLOOR
                   and (p.perk != "last_word" or now >= room.talk_ends)), None)
        if up is not None:
            up.status, up.ends_at = RUNNING, now + config.BS_FLOOR_SECONDS
            changed = True
    floor_busy = any(p.perk in bsperks.TIMED_FLOOR and p.status in (PENDING, QUEUED, RUNNING)
                     for p in room.plays)
    if not room.ballot and now >= room.talk_ends and not floor_busy:
        room.ballot = True
        changed = True
    return changed


def _score(room: BullshitRoom) -> bsquiz.RoundResult:
    guesses = {s.id: s.pick for s in room.guessers() if s.pick is not None}
    bluffs = {s.id: s.bluff for s in room.speakers() if s.bluff is not None}
    uses = [bsperks.Use(p.perk, p.by, p.target, p.option, p.slips)
            for p in room.live_plays() if p.ref is None and p.status != PENDING]
    crowned = next((p.option for p in room.live_plays("kingmaker")), None)
    return bsperks.score(room.n, room.panel, guesses, bluffs, uses, crowned)


# --- host steps -----------------------------------------------------------

def open_discussion(room: BullshitRoom, now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    if room.phase != "briefing":
        raise Rejected("The discussion is not waiting to open.")
    waiting = len(room.speakers()) - room.speakers_in()
    if waiting:
        raise Rejected(f"Waiting on {waiting} speaker{'s' if waiting != 1 else ''}.")
    speakers = len(room.panel.speakers)
    room.phase = "open"
    room.ballot = False
    room.talk_ends = now + config.BS_TALK_SECONDS_PER_SPEAKER * speakers
    room.openings_end = now + config.BS_OPENING_SECONDS_PER_SPEAKER * speakers
    tick(room, now)


def _talk_step(room: BullshitRoom) -> None:
    if room.phase != "open":
        raise Rejected("There is no discussion running.")
    if room.ballot:
        raise Rejected("Voting is already open.")


def extend_talk(room: BullshitRoom, now: float | None = None) -> None:
    """The host's "+30 s"."""
    now = time.monotonic() if now is None else now
    _talk_step(room)
    room.talk_ends = max(room.talk_ends, now) + config.BS_TALK_EXTEND_SECONDS


def end_talk(room: BullshitRoom, now: float | None = None) -> None:
    """The host's "End the talking". Last Words still get their turn, then
    the ballot opens."""
    now = time.monotonic() if now is None else now
    _talk_step(room)
    room.talk_ends = min(room.talk_ends, now)
    room.openings_end = min(room.openings_end, now)
    tick(room, now)


def floor_done(room: BullshitRoom, now: float | None = None) -> None:
    """The host ends whatever floor perk is running, early."""
    now = time.monotonic() if now is None else now
    running = next((p for p in room.plays if p.status == RUNNING), None)
    if running is None:
        raise Rejected("Nothing is running.")
    running.ends_at = now
    tick(room, now)


def slip(room: BullshitRoom, play_id: object, step: object = 1) -> None:
    """The host counts a Forbidden Words slip (or takes one back)."""
    if room.phase not in BEFORE_LOCK:
        raise Rejected("Slips only count until the votes lock.")
    play = next((p for p in room.live_plays("forbidden_words") if p.id == play_id), None)
    if play is None or play.status != LIVE:
        raise Rejected("Those words aren't banned right now.")
    if step not in (1, -1):
        raise Rejected("One slip at a time.")
    play.slips = max(0, min(config.BS_FORBIDDEN_MAX_SLIPS, play.slips + step))


def lock(room: BullshitRoom, force: bool = False, now: float | None = None) -> None:
    """Close guessing and show the headcount, not yet the truth. Waits only
    on online guessers; an offline one who never guessed casts no vote.

    `force` is the host's "Lock anyway", for a guesser who is connected but
    not answering: they cast no vote and spend no wager, exactly as if they
    had gone offline. Nothing is guessed on their behalf.
    """
    if room.phase != "open":
        raise Rejected("There is no discussion to lock.")
    if not room.ballot:
        raise Rejected("Voting hasn't opened yet. End the talking first.")
    waiting = len(room.awaited_guessers()) - room.guessers_in()
    if waiting and not force:
        raise Rejected(f"Waiting on {waiting} guesser{'s' if waiting != 1 else ''}.")
    for play in room.plays:
        if play.status == PENDING:
            _land(room, play)
    room.result = _score(room)
    room.phase = "votes"


def reveal(room: BullshitRoom, now: float | None = None) -> None:
    """Show the truth and apply every score change, exactly once: only this
    transition adds to totals, and it can only happen from `votes`. Every
    perk played this round comes out; a Hostile Takeover that came good is
    announced and gets its reaction window before the totals swap."""
    now = time.monotonic() if now is None else now
    if room.phase != "votes" or room.result is None:
        raise Rejected("Lock the votes first.")
    room.result = _score(room)
    for player_id, line in room.result.lines.items():
        room.seats[player_id].total += line.delta
    room.history.append(_history_entry(room))
    room.phase = "reveal"
    for play in room.live_plays("hostile_takeover"):
        line = room.result.lines.get(play.by)
        if line is not None and line.role == GUESSER and line.won:
            play.status, play.settles_at = PENDING, now + config.BS_REACTION_SECONDS
        else:
            play.status = DONE


def _history_entry(room: BullshitRoom) -> dict[str, Any]:
    """One revealed round, as the history list shows it."""
    q = room.question()
    return {
        "round": len(room.played) + 1,
        "prompt": q.prompt,
        "options": [{"key": o.key, "text": o.text, "speaker": o.speaker}
                    for o in room.panel.options],
        "truth": room.panel.correct_key,
        "author": room.panel.author,
        "bullshitters": list(room.panel.bullshitters),
        "explanation": q.explanation,
        "counts": dict(room.result.counts),
        "winner": room.result.winner,
        "deltas": {pid: line.delta for pid, line in room.result.lines.items()},
        "perks": [{"name": bsperks.PERK[p.perk].name, "by": p.by, "target": p.target}
                  for p in room.live_plays() if p.ref is None],
    }


def mark_gone(room: BullshitRoom, player_id: str) -> None:
    """The host's "Mark gone": apply the leaving rule now instead of after the
    grace period, for somebody who has stopped responding. Same consequences
    as really leaving (relegated for the rest of the game, swapped out of a
    briefing); if they come back they can still guess."""
    if room.phase == "lobby":
        raise Rejected("Nobody can leave a game that has not started.")
    if player_id not in room.seats:
        raise Rejected("That player is not in this game.")
    on_disconnect(room, player_id)


def next_round(room: BullshitRoom, now: float | None = None) -> None:
    """Move on from a reveal, or retry dealing after a wait. Anything still
    inside its reaction window at the reveal lands first."""
    if room.phase == "reveal":
        for play in room.plays:
            if play.status == PENDING:
                _land(room, play)
        room.revealed += [(p.by, p.perk) for p in room.live_plays()]
        room.played.append(room.current)
    elif room.phase != "waiting":
        raise Rejected("Reveal this round first.")
    _deal_next(room)
