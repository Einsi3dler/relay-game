# Bullshit Quiz — Game Definition and Handoff

Version: 1.0 draft  
Date: 20 October 2026  
Purpose: give a builder or handoff tool the complete agreed game concept, scoring, player actions, and how each rule question the handoff left open was settled.

## 0. Decisions settled by the owner (20 October 2026)

These settle every question the original handoff left open (section 11).
Where this section and anything later in the document disagree, this section
wins.

| Question | Decision |
| --- | --- |
| Wager-number reuse | **Single-use.** Each player starts with 1 through N - 1 and spends one on every wagering turn, win or lose. |
| Tied top vote counts | **Nobody wins the crowd**, as the handoff proposed (section 11). |
| Source of false options | **The author writes them** when submitting the question. The server tells each author how many options to write (2, 3 or 4), which fixes B for that question. |
| Who runs the game | **A separate, non-playing host** behind a host key, like Guess Who. |
| Who plays (revised 21 October 2026) | **Players join through the site.** They sign up or sign in to a Game Night account and join with the host's code. No roster file, no email addresses, no invite links; nothing mails anybody. |
| Perks (23 October 2026) | **One unique perk per player for the game, chosen in a draft before the quiz.** A random order, 15 seconds a pick, and players can trade places by swapping positions and one wager card each. At most 30 players, one per perk. The full design: [BULLSHIT_QUIZ_PERKS.md](BULLSHIT_QUIZ_PERKS.md). |
| Wager cards (23 October 2026, refines "single-use"; hand size revised 27 October) | **Wagers are cards, not numbers.** Everybody starts with one card per round, spread evenly from 1 to N-1 (`bsquiz.wager_hand`; with N-1 rounds that is exactly one card per value). Draft trades move cards, so a hand can hold two 19s and no 1. Each wagering turn spends one card, so nobody runs out, and whoever is truthful in some rounds ends with cards to spare. |
| The discussion clock (23 October 2026, supersedes "no timers" for this step) | **Guessers vote after the talking.** The discussion runs on a clock (`BS_TALK_SECONDS_PER_SPEAKER`, 60 s per speaker) and the ballot opens when it runs out, so the speaking perks reach everybody. |
| Players who leave (supersedes "pause" in section 11) | **The game never waits for them.** Once a game has started, going offline relegates a player for the rest of that game: they keep their seat and score, are never picked to bluff again (even after returning), and are never waited on as a guesser. Their question is skipped while they are away and stays queued; if they return it can still be played, and if every remaining question belongs to somebody offline, those are dropped and the game ends. |
| The question bank (27 October 2026, supersedes "one question each" and "everyone truthful once") | **Everybody writes three questions, the host reviews them blind, and a draw fills the rounds.** Before sharing the link the host sets how many rounds the night has. Each player writes `BS_QUESTIONS_PER_PLAYER` (3) questions, each asked for 2, 3 or 4 options. The host reads every one without its author, may tidy the wording or the options (same number of options; the author sees the tidied version, since they defend it), and tags each **Fascinating**, **Mid ground**, **Bland** or **Leave out**. At the start `bsquiz.plan_rounds` fills the rounds with a weighted draw: better tags are likelier, an author's second and third pick less likely, Leave out never. Nobody is promised a turn, so nobody can work out who must be truthful from who hasn't been yet. The night opens and closes on its best questions, and the finale gets the biggest panel. Weights are provisional (`BS_TAG_WEIGHTS`, `BS_REPEAT_WEIGHTS`). |

The handoff's proposed defaults were taken for everything else:

- **Commitment timing.** Bullshitters lock their wager in the briefing,
  before the discussion; guessers commit after it.
- **The reveal.** The vote distribution is shown before the truth.
- **Scores.** Everybody starts at zero, and totals may go negative.
- **Missing votes are never invented.** The round waits for guessers who are
  online until the host presses **Lock anyway**; a guesser who casts no vote
  scores nothing and spends no wager. Players who have left are not waited
  on at all ("Players who leave" in the table).
- **A final tie** gives joint winners.
- **Lobby and panel size.** At least three players (`BS_MIN_PLAYERS`). Each
  question asks for 2, 3 or 4 options, with equal chance, drawn when its
  author joins; the deal uses fewer if the room is too small or too few
  players are online.
- **Which questions, and in what order**: the question bank's draw (the row
  above), made when the host starts the game.
- **Pacing.** The host presses every step except two: a draft pick running
  out, and the discussion clock opening the ballot.

One addition the handoff does not cover: during the briefing the truthful
player confirms "ready" in the same beat as the bullshitters lock their
wagers. Otherwise the one speaker who never locks in is the truthful one.
(Guess Who learned this the hard way; see docs/QUIZ_ROLL_CALL.md section 2.)

## 0.1 Running a session

Where things live: rules `backend/bsquiz.py`, room `backend/bsroom.py`,
seats and questions `backend/bsroster.py`, web routes `backend/bsserver.py`,
screens `frontend/bullshit*.{html,js,css}`, perks `backend/bsperks.py` and
`docs/BULLSHIT_QUIZ_PERKS.md`.

1. Set `RELAY_BULLSHIT_HOST_KEY` (the default is `dev`, in a public repo) and
   open `/bullshithost`. Set **Rounds tonight** first. The lobby shows a
   **join code** and a link (`/bullshit?code=K7QM`) with a Copy button.
2. Share the link (or just the code) in the group chat. It can go out a day
   early: the code, the round count and every question are saved, so a
   restart overnight changes nothing. Only **New game night** makes a new
   code.
3. Players open it. Signed out, they sign up or sign in to Game Night like
   on any website and come straight back; the code is filled in from the
   link, and **Join** gives their account a seat. Their in-game name is their
   first name (plus a last initial if two players share one). They write
   three questions, each with its own option count (2, 3 or 4); the lobby
   shows how many each has written. The account is the seat: signing in
   anywhere and opening `/bullshit` (or the hub card) resumes it.
4. **Review the bank**, under the lobby: open **Review the questions**. It
   is folded because every answer is in it, so not while screen-sharing,
   and it never shows who wrote what. Tag every question; **Edit** tidies the
   wording or the options. An author who changes a question you already
   tagged sends it back to review. While people are still writing, a new
   question appearing just as a name ticks over can give its author away,
   so do the review once everyone is done.
5. **Start the game** needs at least three players, every question from
   every one of them, and every question tagged. It draws tonight's
   questions and their order, fixes N, and from then on the code lets
   nobody else in. It opens the **perk draft**: everybody sees the picking
   order and can trade places; press **Start the picks** when the haggling
   is done (each pick then gets 15 seconds), and **Start the quiz** once
   every perk is taken.
6. Each round the host presses: **Open the discussion** (once every speaker
   has committed), **Lock the votes** (once every guesser has), **Reveal the
   truth**, **Next round**. One thing runs on its own: the discussion clock.
   Guessers can't vote until it runs out (or the host presses **End the
   talking**) and any Last Words have been said.
7. Host tools, all on the host page:
   - **Remove** on a player's chip in the lobby, for somebody who joined by
     mistake. Their question goes too; they can join again with the code.
   - **New game night**: everyone and every question cleared, and a new
     code, so last week's link stops working. Not undoable.
   - **Mark gone** on any player's chip mid-game: the leaving rule, applied
     now instead of after the grace period. If they act again they count as
     back (still guessing only).
   - **Lock anyway** when a guesser is connected but not answering: they cast
     no vote that round and lose nothing.
   - **Undo** for 10 seconds after **Reset the room** (`BS_UNDO_SECONDS`).
     Reset keeps everyone, their questions and the code.
   - **Round history** (collapsed, host only): every revealed round, with
     the truth, who bluffed and the vote split. Players never receive it;
     open it only when you are not screen-sharing.
   - **+30 s** and **End the talking** on the discussion clock, and **Done,
     next** to cut a running Hot Seat, Mute Button or Last Word short.
   - **Slip +1** (and **Undo**) on each Forbidden Words, every time the
     speaker says one.
   - **Secret perks to referee** (collapsed, host only): who False Witness
     recruited, so you can hold them to it.
   Players get sounds and a buzz when it is their turn, and a mute button.

Leaving mid-game (owner's rule, 21 October 2026). Going offline after the
start relegates a player to guessing for the rest of the game. 
"Offline" means gone for longer than the grace period
(`BS_GRACE_SECONDS`, 30 seconds): phones drop the connection whenever the
screen locks, so a dropped socket first only shows the player as
*reconnecting* on the host screen, and the game carries on treating them as
present. Only if they have not reopened their link by then does the rule
below apply. In detail:

- An offline guesser is not waited on. A vote they locked before leaving
  still counts; if they never voted, they score nothing and spend no wager.
- If it is an offline player's question's turn, the next one is dealt and
  theirs waits. When everything left is from offline players, those
  questions are dropped and the game ends; the final screen says how many.
- In the briefing (before the discussion opens), a bullshitter who leaves is
  replaced by another player who may still bluff, or their option is removed
  if nobody can take it; an author who leaves sends the question back to the
  queue. Wagers locked in a round that does not happen are refunded.
- After the discussion opens, the round carries on and commitments stand.
- If too few players are online to deal anything, the host sees **Waiting
  for players** and retries once people reopen their links.

Signing back in (on any device) and opening `/bullshit` resumes the seat.

## 1. Read this first

The core rules below reflect the creator's explanations and corrections. The original handoff marked some details as open decisions or proposed defaults. All of them are now settled: each is marked **Settled** where it comes up, and section 0 records the decisions.

This document defines gameplay and the information the game needs to manage. Players communicate through an external group call. The choice of application framework, hosting, authentication, and call platform is outside this specification.

## 2. The concept

Bullshit Quiz is an individual multiplayer quiz built around persuasion, deception, and confidence wagers. Everyone contributes one question whose answer they expect the other players will not know.

For each question, its author defends the correct answer. One, two, or three other players are randomly assigned incorrect answers. These players are the **bullshitters**. Each speaker tries to convince the audience to choose their own answer.

The audience chooses whom to believe and wagers points on being correct. Bullshitters also wager, but their wager is on attracting the largest number of voters. The truthful player has a separate reward and does not wager on their own question.

The intended experience includes changing numbers of answer options, unpredictable repeat appearances as a bullshitter, competing explanations, questioning on the call, and consequential reveals. Bullshitters compete individually against the truthful player and against one another.

## 3. Game length and player count

Let **N** be the total number of registered players in the game. Count every player, including the truthful player and all bullshitters. N is not the number of audience members in a particular round.

The original handoff had exactly N questions and N rounds, one truthful turn each. The question bank (section 0, 27 October 2026) replaced that:

- The host sets the number of rounds, **R**, before the night (`BS_ROUNDS_DEFAULT` is 12, at most `BS_ROUNDS_MAX`).
- Every player writes three questions. R of them are played, or every playable one if there are fewer. Each is played at most once.
- A player is the truthful player once for each of their questions played: possibly several times, possibly never.
- Every other turn is a wagering turn, either as a guesser or as a bullshitter.
- Each player starts with **R wager cards**, so there is one for every turn even for somebody who is never truthful.

For example, 20 players and 20 rounds: most players are truthful once, roughly one in five twice and one in five never, and each starts with 20 cards from 1 to 19.

Bluffing assignments can repeat across rounds. There is no guaranteed equal number of bluffing appearances. Random selection may give a player several appearances, one appearance, or none. Preserve this variability.

The roster and N should remain fixed once the game begins, so question count, wager limits, and rewards stay consistent. Handling departures is addressed under operational decisions below.

## 4. Questions and answer options

Each player privately submits a question and its correct answer. The question should have one clear answer that the author expects the group will not already know. It may concern an obscure fact or a personal experience, provided the question does not immediately reveal which speaker must be the author.

At minimum, retain the question text, correct answer, and submitting player's identity. A short explanation for the reveal can also be collected.

The author is the truthful speaker for that question. Their identity **as the author** must stay hidden from the other players until the reveal. Otherwise, knowing who submitted the question would identify the correct speaker immediately.

Each round contains exactly one correct option and one incorrect option per bullshitter. With one bullshitter there are two options; with two there are three; with three there are four. Each option belongs to one speaker, and each speaker defends one option.

All options must be distinct. The answer key is fixed before discussion and cannot change in response to votes or wagers.

**Settled: the author writes the incorrect answers** (section 0). When they submit their question, the server asks them for 2, 3 or 4 options in total: the real answer plus one decoy per bullshitter. Bullshitters never invent their own option; being assigned an option to defend is part of the concept.

## 5. Roles in each round

### Truthful player

This is the author of the current question. They know the correct answer and defend it on the call. They do not submit a guessing vote or a wager for this round. Their score depends on whether their option attracts the largest crowd.

### Bullshitters

Randomly choose **B** distinct players from everyone except the question author, where B is normally 1, 2, or 3. Assign each one a different incorrect option.

Bullshitters know that their assigned answer is incorrect. They try to persuade guessers to select it and submit a wager on their own ability to attract the largest crowd. They do not also cast an audience vote or make a second wager on which answer is true.

Being a bullshitter in one round does not prevent being selected again later. The same player cannot hold multiple speaker roles within one round.

### Guessers

Every remaining player is a guesser. They listen, ask questions, select exactly one answer, and wager on whether that answer is correct.

The number of guessers in a round is:

```text
G = N − 1 − B
```

With 20 players and three bullshitters, there are 16 guessers. With 15 players and one bullshitter, there are 13 guessers.

Only guessers contribute votes to the crowd counts. Every vote has equal weight.

**Settled: small groups** (section 0). At least three players, and B is never more than min(3, N − 2), so at least one guesser remains. Each author is asked for 2, 3 or 4 options with equal chance, and the deal uses fewer bullshitters when the room cannot fill them all.

## 6. Wagers

A wager is a whole number **w** in this range:

```text
1 ≤ w ≤ N − 1
```

With 15 players, wagers range from 1 to 14. With 20 players, they range from 1 to 19. The limit uses the total player count, not the number of guessers in the round.

A successful wager adds w points to the player's score. An unsuccessful wager deducts w points from their score. Wrong guesses do not merely earn zero.

Guessers wager on choosing the correct answer. Bullshitters wager on their own answer attracting the largest crowd. These are different success conditions.

**Settled: wagers are single-use cards** (section 0). Each player starts with one card per value from 1 through N − 1 and spends one on every guessing or bluffing turn, win or lose. Trades in the perk draft move cards between players, so a hand can hold two of one value and none of another. The scoring rule above is unchanged: an unsuccessful wager is still deducted.

**Settled: score handling** (section 0). Everyone starts at zero, totals may go negative, and every card in a player's hand is a legal wager whatever their current score. This keeps the fixed schedule playable after losses. There is no score floor, and nobody is eliminated for a negative score.

## 7. Scoring

### Count people, not wager amounts

For each answer, count the guessers who selected it. These headcounts determine who attracted the largest crowd. A voter wagering 1 and a voter wagering 19 each contribute exactly one vote.

For a round without a tie for first place, the speaker with more votes than every other speaker wins the crowd. They do not need more than half of all votes. Compare against **all** speakers, including the truthful player.

A tie for the top count means nobody wins the crowd, unless a Kingmaker perk picks one of the tied options. Section 11 gives the full rule.

### Guesser score

```text
If the chosen answer is correct:
    round score = +w

If the chosen answer is incorrect:
    round score = −w
```

### Bullshitter score

Let **v** be the number of guessers who chose that bullshitter's assigned answer. They earn **one point per person fooled**, independently of the wager outcome.

```text
If their answer wins the crowd:
    round score = v + w

If their answer does not win the crowd:
    round score = v − w
```

A bullshitter keeps the points from people fooled even when losing their wager. Their net round score can be positive, zero, or negative after a failed wager.

Winning the crowd does not grant a bullshitter an additional N-point bonus. The N-or-half-N reward belongs exclusively to the truthful player.

### Truthful player score

```text
If the correct answer wins the crowd:
    round score = N

Otherwise:
    round score = floor(N / 2)
```

For 15 players, this means 15 or 7 points. For 20 players, it means 20 or 10. For 21 players, it means 21 or 10.

The truthful player has no wager to lose and does not additionally earn one point per supporter. Their complete round reward is the applicable amount above.

### Total score and winner

Add each player's round score to their running total. After all N questions have been played, the player with the highest total score wins.

Scores are awarded or deducted independently. A bullshitter does not receive the points that a deceived guesser wagered. Total points awarded and deducted do not need to balance.

## 8. Round flow

1. **Select the next planned question.** Its author becomes the truthful player. Tonight's questions and their order are drawn at the start (section 0, the question bank).
2. **Select the bluffing panel.** Randomly choose the number of bullshitters, select that many distinct eligible players, and assign their incorrect options.
3. **Deliver private role information.** Each speaker learns their own role and assigned answer. Other players must not receive the answer key or hidden author identity.
4. **Present the question and options.** Show which player is defending each option without revealing who is truthful.
5. **Discuss on the group call.** Speakers defend their answers. Guessers can question them, challenge explanations, and decide whom to trust.
6. **Collect commitments.** Every guesser submits one answer and one wager. Every bullshitter submits one wager attached to their assigned option. The truthful player submits neither.
7. **Lock the round.** Finalize all valid votes and wagers before displaying results. Resolve any missing submissions under the agreed operational policy.
8. **Reveal and score.** Show the correct answer, the truthful player, the bullshitters, each option's vote count, and each player's score change. Update the leaderboard once.
9. **Advance or finish.** Continue to the next unused question, or show the final results after the last planned round.

**Settled: commitment timing** (section 0). Bullshitters lock their wagers in the briefing, after they see their assigned answers and before the discussion opens; the truthful player confirms "ready" at the same moment. Guessers decide their answer and wager after the discussion.

**Settled: reveal presentation** (section 0). The final distribution of votes is shown first, then the truth and the score changes. Both come after commitments are locked. This adds suspense without changing the scoring rules.

**Settled: the discussion clock** (section 0). The discussion runs on a clock, 60 seconds per speaker (`BS_TALK_SECONDS_PER_SPEAKER`), and the ballot opens when it runs out or the host ends the talking. The host paces every other step of a round.

## 9. Visibility and player actions

Before their round, submitted questions, correct answers, and author identities remain private. During a round, the public view can show the question, answer options, speaker names, round number, and previous-round leaderboard.

The truth-versus-bluff role labels remain hidden until the reveal. The truthful player privately sees their own role. Each bullshitter privately sees their own role and assigned answer. Guessers see the choices without an answer key.

Individual guesses, wagers, and live vote totals should remain private until the round locks. A player's own pending submission can be visible to that player. Showing a generic count of completed submissions is compatible with this; exposing which answer is leading is not part of the agreed game.

On the guessing interface, a player needs one answer choice and one wager. On the bluffing interface, a player needs their assigned answer and one wager; they have no guessing ballot. On the truthful interface, a player needs their question and correct answer, with no wager control.

Hidden answers and role information must not be sent to unauthorized players merely because the interface conceals them visually. After reveal, the round results may expose those details.

## 10. Worked examples

### Example A: a bullshitter wins the crowd

There are 20 players, one truthful speaker, three bullshitters, and 16 guessers. Wagers may range from 1 to 19.

The truthful answer receives six votes. Bullshitter A receives seven, B receives two, and C receives one. These counts sum to 16.

- The truthful player earns **10** points: floor(20 / 2).
- A wagered eight and wins the crowd: **7 + 8 = 15** points.
- B wagered four and loses that wager: **2 − 4 = −2** points.
- C wagered one and loses that wager: **1 − 1 = 0** points.
- A guesser who chose the truthful answer with a wager of nine earns **+9**.
- A guesser who chose any false answer with a wager of nine earns **−9**.

A receives no additional 20-point reward. The guessers' wager sizes do not affect A's seven-person crowd.

### Example B: the truth wins without an absolute majority

There are 15 players, one truthful speaker, two bullshitters, and 12 guessers. The correct answer receives five votes, A receives four, and B receives three.

- The truthful player earns **15** points, because five is the largest vote count even though it is fewer than half of the 12 votes.
- A wagered six and earns **4 − 6 = −2** points.
- B wagered two and earns **3 − 2 = +1** point.

B loses the wager but still finishes the round with a positive score because the points for fooling three people are retained.

### Example C: odd player counts

With 21 total players, the truthful reward is **21** for winning the crowd and **10** otherwise. The fallback is rounded down from 10.5. The wager limit is **20**. These values do not change when the number of bullshitters changes.

## 11. Questions the handoff left open, and how they were settled

### Wager-number reuse

**Settled: single-use cards.** Each player holds one wager card per value from 1 through N − 1 and spends one on every wagering turn, so the N − 1 wagering turns of a full game use the hand up exactly. Section 6 has the detail.

### Tied top vote counts

**Settled: an outright lead is required.** If multiple options tie for the largest positive vote count, no speaker wins the crowd. The truthful player receives floor(N / 2). Every bullshitter loses their wager while retaining their own points per person fooled. Guesser scoring remains based on the correct answer, so guessers who chose it still gain their wagers even if its vote count is tied. Nobody wins the crowd when nobody votes. A Kingmaker perk can make one of the tied options the winner ([BULLSHIT_QUIZ_PERKS.md](BULLSHIT_QUIZ_PERKS.md)).

The definition of crowd victory stays separate from the score formulas (`crowd_winner` in `backend/bsquiz.py`), so a different tie policy would not change those formulas.

### Source of false options

**Settled: the author writes them** with their question, as many as the server asks for (section 4). One speaker per option and one correct answer overall still hold. The deal never builds a round with a missing option: if the room cannot fill every decoy, it uses fewer bullshitters and leaves the spare decoys out.

### Missing submissions and disconnected players

N stays fixed. A disconnect is not a reason to recalculate everyone's wager cap or the truthful reward during play.

**Settled: the game never waits for a player who has left** (section 0, "Players who leave", and section 0.1). A missing vote is never invented: a guesser who casts no vote scores nothing and spends no wager, and the host can lock a round without a guesser who is online but not answering. A question whose author is offline is skipped and stays queued. It is dropped only when every question left belongs to somebody offline.

### Final leaderboard ties

**Settled: joint winners.** An extra tiebreaker round would change the fixed N-round structure and should be added only as a deliberate rule change.

### Other operational defaults

**Settled** (section 0): scores start at zero and may go negative; bullshitters lock before the discussion and guessers after it; which questions are played, and in what order, is the question bank's draw (section 0); each question asks for 2, 3 or 4 options with equal chance; the lobby needs at least three players; and the discussion runs on a clock.

## 12. Minimum information to retain

The implementation can use its existing architecture. It needs enough information to reconstruct and explain the following:

- **Game:** registered players, fixed N, ordered question schedule, active round, round phase, and the policies settled in section 0.
- **Player:** stable identity, display name, total score, submitted question, and the wager cards still in their hand.
- **Question:** author, prompt, correct answer, incorrect options once supplied, and optional reveal explanation.
- **Round:** question, truthful player, selected bullshitters, option-to-speaker assignments, and which option is correct.
- **Commitments:** each guesser's chosen option and wager, each bullshitter's wager, and whether the submission is locked.
- **Results:** vote counts, crowd-victory outcome under the selected tie policy, score components, and final score changes for each player.

Record enough detail to show a bullshitter's result as separate components, such as three people fooled, an eight-point wager lost, and a net change of minus five. Apply each round's score changes exactly once, even if the result screen is reopened.

## 13. Acceptance checklist

- A game plays the rounds the host set (or every playable question, if fewer), each question at most once, and promises nobody a truthful turn.
- Every player has a wagering role in every round except their own truthful round.
- Every round has one correct answer and one distinct incorrect option per selected bullshitter.
- Repeated bluffing appearances are allowed; equal bluffing turns are not enforced.
- Only guessers cast votes, and each casts at most one valid vote.
- All wagers are whole numbers from 1 through N − 1, each played from the player's hand of single-use wager cards.
- Incorrect guessing deducts the wager from the score.
- A bullshitter receives one point per voter plus or minus their own wager, using crowd victory as the wager's success condition.
- Bullshitters compete against every option, including the truthful one, for crowd victory.
- The truthful player alone receives N or floor(N / 2), with no extra per-voter reward.
- Crowd counts use the number of people, never the sum of their wagers.
- The correct answer, author identity, hidden roles, and pending commitments are not exposed prematurely.
- Worked examples A, B, and C produce the stated outcomes.
- Ties and incomplete rounds use an explicit selected policy rather than accidental implementation behavior.
- Score changes are applied once per completed round, and the final winner is determined after the last round.

