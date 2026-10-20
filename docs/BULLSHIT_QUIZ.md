# Bullshit Quiz — Game Definition and Handoff

Version: 1.0 draft  
Date: 20 October 2026  
Purpose: give a builder or handoff tool the complete agreed game concept, scoring, player actions, and outstanding rule decisions.

## 0. Decisions settled by the owner (20 October 2026)

These close the open decisions in section 11. Where this section and a later
"Open decision" or "Proposed default" disagree, this section wins.

| Question | Decision |
| --- | --- |
| Wager-number reuse | **Single-use.** Each player starts with 1 through N - 1 and spends one on every wagering turn, win or lose. |
| Tied top vote counts | **Nobody wins the crowd** (the proposed default in section 11). |
| Source of false options | **The author writes them** when submitting the question. The server tells each author how many options to write (2, 3 or 4), which fixes B for that question. |
| Who runs the game | **A separate, non-playing host** behind a host key, like Guess Who. |
| Roster | **Bullshit Quiz has its own roster** and its own invite links. Nothing mails it. |
| Players who leave (supersedes "pause" in section 11) | **The game never waits for them.** Once a game has started, going offline relegates a player for the rest of that game: they keep their seat and score, are never picked to bluff again (even after returning), and are never waited on as a guesser. Their question is skipped while they are away and stays queued; if they return it can still be played, and if every remaining question belongs to somebody offline, those are dropped and the game ends. |

Taken as the proposed defaults: bullshitters lock their wager before
discussion, guessers commit after it; the vote distribution is shown before
the truth; scores start at zero and may go negative; a missing submission
pauses the round rather than being invented; joint winners on a final tie;
no timers, the host paces every step.

One addition the handoff does not cover: during the briefing the truthful
player confirms "ready" in the same beat as the bullshitters lock their
wagers. Otherwise the one speaker who never locks in is the truthful one.
(Guess Who learned this the hard way; see docs/QUIZ_ROLL_CALL.md section 2.)

## 0.1 Running a session

Where things live: rules `backend/bsquiz.py`, room `backend/bsroom.py`,
roster and questions `backend/bsroster.py`, web routes `backend/bsserver.py`,
screens `frontend/bullshit*.{html,js,css}`.

1. Put the players in `var/bullshit/roster.json` (gitignored, never commit it):
   `{"participants": [{"email": "..."}, ...]}`. Each newcomer is given their
   option count (2, 3 or 4) on import, from the roster's size.
2. `python -m backend.bsroster import`, then `RELAY_BASE_URL=https://your.host
   python -m backend.bsroster links` prints one personal link per player.
   Hand them out yourself. **Nothing emails anybody**; see
   docs/ROLL_CALL_DO_NOT_SEND.md. Running `links` again replaces every link.
3. Set `RELAY_BULLSHIT_HOST_KEY` (the default is `dev`, in a public repo) and
   open `/bullshithost`.
4. Players open their link once. Signed out, they sign in or make a Game
   Night account and come straight back; then **Claim my seat** ties the
   invitation to their account. Their in-game name is their first name (plus
   a last initial if two players share one). They write their question, and
   the host lobby shows who has written. From then on the account is the
   seat: signing in anywhere and opening `/bullshit` (or the hub card)
   resumes it, and the link alone opens nothing. **Start the game** needs at least three
   players and a question from every one of them; it fixes N and the order.
5. Each round the host presses: **Open the discussion** (once every speaker
   has committed), **Lock the votes** (once every guesser has), **Reveal the
   truth**, **Next round**. Nothing has a timer.
6. Host tools, all on the host page:
   - **Invitations** (collapsed; it shows email addresses, so do not open it
     while screen-sharing): who has claimed and written, and **New link** for
     somebody who lost theirs. Links are stored hashed, so a new link
     replaces the old one; claimed seats need no link, just a sign-in.
   - **Mark gone** on any player's chip mid-game: the leaving rule, applied
     now instead of after the grace period. If they act again they count as
     back (still guessing only).
   - **Lock anyway** when a guesser is connected but not answering: they cast
     no vote that round and lose nothing.
   - **Undo** for 10 seconds after **Reset the room** (`BS_UNDO_SECONDS`).
   Everyone also gets a **Past rounds** list from the first reveal, sounds
   and a buzz when it is their turn, and a mute button.

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

The core rules below reflect the creator's explanations and corrections. Details labelled **Proposed default** are suggestions for a first playable version, not previously agreed rules. Items labelled **Open decision** must remain explicit rather than being silently treated as settled.

This document defines gameplay and the information the game needs to manage. Players communicate through an external group call. The choice of application framework, hosting, authentication, and call platform is outside this specification.

## 2. The concept

Bullshit Quiz is an individual multiplayer quiz built around persuasion, deception, and confidence wagers. Everyone contributes one question whose answer they expect the other players will not know.

For each question, its author defends the correct answer. One, two, or three other players are randomly assigned incorrect answers. These players are the **bullshitters**. Each speaker tries to convince the audience to choose their own answer.

The audience chooses whom to believe and wagers points on being correct. Bullshitters also wager, but their wager is on attracting the largest number of voters. The truthful player has a separate reward and does not wager on their own question.

The intended experience includes changing numbers of answer options, unpredictable repeat appearances as a bullshitter, competing explanations, questioning on the call, and consequential reveals. Bullshitters compete individually against the truthful player and against one another.

## 3. Game length and player count

Let **N** be the total number of registered players in the game. Count every player, including the truthful player and all bullshitters. N is not the number of audience members in a particular round.

- There are exactly **N questions and N rounds**.
- Every player contributes exactly one question.
- Every player's question is used exactly once.
- Every player is the truthful player exactly once.
- Every other turn is a wagering turn, either as a guesser or as a bullshitter.
- Each player therefore has **N − 1 wagering turns**.

For example, 20 players means 20 questions, one truthful turn per player, and 19 wagering turns per player.

Bluffing assignments can repeat across rounds. There is no guaranteed equal number of bluffing appearances. Random selection may give a player several appearances, one appearance, or none. Preserve this variability.

The roster and N should remain fixed once the game begins, so question count, wager limits, and rewards stay consistent. Handling departures is addressed under operational decisions below.

## 4. Questions and answer options

Each player privately submits a question and its correct answer. The question should have one clear answer that the author expects the group will not already know. It may concern an obscure fact or a personal experience, provided the question does not immediately reveal which speaker must be the author.

At minimum, retain the question text, correct answer, and submitting player's identity. A short explanation for the reveal can also be collected.

The author is the truthful speaker for that question. Their identity **as the author** must stay hidden from the other players until the reveal. Otherwise, knowing who submitted the question would identify the correct speaker immediately.

Each round contains exactly one correct option and one incorrect option per bullshitter. With one bullshitter there are two options; with two there are three; with three there are four. Each option belongs to one speaker, and each speaker defends one option.

All options must be distinct. The answer key is fixed before discussion and cannot change in response to votes or wagers.

**Open decision — creating the incorrect answers:** the creator has established that bullshitters are assigned wrong answers, but has not specified who writes those answers. A possible workflow is for the question author to submit three plausible decoys privately, from which the required number is selected. Host-written decoys are another option. Do not silently require bullshitters to invent their own option; being assigned an option to defend is part of the stated concept.

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

**Proposed default — small groups:** require at least three players and select B from 1 through min(3, N − 2), ensuring at least one guesser remains. The creator has not set a formal minimum lobby size or the probabilities for selecting each panel size.

## 6. Wagers

A wager is a whole number **w** in this range:

```text
1 ≤ w ≤ N − 1
```

With 15 players, wagers range from 1 to 14. With 20 players, they range from 1 to 19. The limit uses the total player count, not the number of guessers in the round.

A successful wager adds w points to the player's score. An unsuccessful wager deducts w points from their score. Wrong guesses do not merely earn zero.

Guessers wager on choosing the correct answer. Bullshitters wager on their own answer attracting the largest crowd. These are different success conditions.

**Open decision — whether a wager number can be reused:** losing an unsuccessful wager from the score is confirmed. The discussion does not unambiguously establish whether selecting a number also permanently removes that number from the player's available choices. These are separate mechanics. If numbers are single-use, each player starts with 1 through N − 1 and consumes one on every guessing or bluffing turn, regardless of success. If numbers are reusable, the full range remains available on later turns. Keep this policy separate from score calculation and explicitly settle it before play.

**Proposed default — score handling:** start everyone at zero, allow negative totals, and allow every legal wager regardless of the current score. This keeps the fixed schedule playable after losses. Do not introduce a score floor or eliminate players for negative scores without an explicit rule change.

## 7. Scoring

### Count people, not wager amounts

For each answer, count the guessers who selected it. These headcounts determine who attracted the largest crowd. A voter wagering 1 and a voter wagering 19 each contribute exactly one vote.

For a round without a tie for first place, the speaker with more votes than every other speaker wins the crowd. They do not need more than half of all votes. Compare against **all** speakers, including the truthful player.

Treatment of tied top vote counts is an open decision, described in Section 11.

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

1. **Select the next unused question.** Its author becomes the truthful player. Every submitted question must appear once during the game.
2. **Select the bluffing panel.** Randomly choose the number of bullshitters, select that many distinct eligible players, and assign their incorrect options.
3. **Deliver private role information.** Each speaker learns their own role and assigned answer. Other players must not receive the answer key or hidden author identity.
4. **Present the question and options.** Show which player is defending each option without revealing who is truthful.
5. **Discuss on the group call.** Speakers defend their answers. Guessers can question them, challenge explanations, and decide whom to trust.
6. **Collect commitments.** Every guesser submits one answer and one wager. Every bullshitter submits one wager attached to their assigned option. The truthful player submits neither.
7. **Lock the round.** Finalize all valid votes and wagers before displaying results. Resolve any missing submissions under the agreed operational policy.
8. **Reveal and score.** Show the correct answer, the truthful player, the bullshitters, each option's vote count, and each player's score change. Update the leaderboard once.
9. **Advance or finish.** Continue to the next unused question, or show the final results after N completed rounds.

**Proposed default — commitment timing:** lock bullshitters' wagers before open discussion, after they see their assigned answers. Allow guessers to decide their answer and wager after discussion. This was suggested during development but has not been explicitly confirmed by the creator.

**Proposed default — reveal presentation:** first display the final distribution of votes, then reveal the truth and score changes. Both occur after commitments are locked. This adds suspense without changing the scoring rules.

No specific speech duration, countdown, or round timer has been agreed. A host-controlled discussion and submission window is a possible first version.

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

## 11. Outstanding decisions and proposed defaults

### Wager-number reuse — open decision

Choose whether wager values are reusable or consumed after selection. Both variants still deduct an unsuccessful wager from the score. The fixed N − 1 wagering turns support a single-use set if that is the intended rule, but this handoff does not present it as confirmed.

### Tied top vote counts — open decision

The creator has specified attracting the most people, but has not explicitly accepted a tie rule.

**Proposed default:** require an outright lead. If multiple options tie for the largest positive vote count, no speaker wins the crowd. The truthful player receives floor(N / 2). Every bullshitter loses their wager while retaining their own points per person fooled. Guesser scoring remains based on the correct answer, so guessers who chose it still gain their wagers even if its vote count is tied.

This tie policy is a recommendation, not a confirmed rule. Keep the definition of crowd victory separate from the score formulas so another tie policy can be applied without changing those formulas.

### Source of false options — open decision

Choose who supplies the incorrect answer options. Preserve one speaker per option and one correct answer overall. If fewer than three usable decoys exist, do not silently create an invalid round with missing options.

### Missing submissions and disconnected players — open decision

Keep N and the question schedule fixed. A disconnect is not a reason to recalculate everyone's wager cap or the truthful reward during play.

**Proposed default:** pause and allow reconnection or completion before scoring. Do not automatically invent a guess, spend a wager, or treat a missing vote as a cast vote. If skipping, forfeits, or replacement questions are needed, their consequences require a separate explicit policy.

### Final leaderboard ties — open decision

**Proposed default:** declare joint winners. An extra tiebreaker round would change the fixed N-round structure and should be added only as a deliberate rule change.

### Other operational defaults

Starting at zero with negative scores allowed, the exact timing of wager commitment, the question order, panel-size probabilities, minimum lobby size, and discussion timing have not been explicitly settled. Proposed choices elsewhere in this document should remain identifiable as defaults.

## 12. Minimum information to retain

The implementation can use its existing architecture. It needs enough information to reconstruct and explain the following:

- **Game:** registered players, fixed N, ordered question schedule, active round, round phase, and the selected policies for outstanding decisions.
- **Player:** stable identity, display name, total score, submitted question, and used wager values if the single-use policy is selected.
- **Question:** author, prompt, correct answer, incorrect options once supplied, and optional reveal explanation.
- **Round:** question, truthful player, selected bullshitters, option-to-speaker assignments, and which option is correct.
- **Commitments:** each guesser's chosen option and wager, each bullshitter's wager, and whether the submission is locked.
- **Results:** vote counts, crowd-victory outcome under the selected tie policy, score components, and final score changes for each player.

Record enough detail to show a bullshitter's result as separate components, such as three people fooled, an eight-point wager lost, and a net change of minus five. Apply each round's score changes exactly once, even if the result screen is reopened.

## 13. Acceptance checklist

- A game with N players schedules exactly N questions and gives each player exactly one truthful turn.
- Every player has a wagering role in every round except their own truthful round.
- Every round has one correct answer and one distinct incorrect option per selected bullshitter.
- Repeated bluffing appearances are allowed; equal bluffing turns are not enforced.
- Only guessers cast votes, and each casts at most one valid vote.
- All wagers are whole numbers from 1 through N − 1, with any single-use restriction enforced only if that policy is selected.
- Incorrect guessing deducts the wager from the score.
- A bullshitter receives one point per voter plus or minus their own wager, using crowd victory as the wager's success condition.
- Bullshitters compete against every option, including the truthful one, for crowd victory.
- The truthful player alone receives N or floor(N / 2), with no extra per-voter reward.
- Crowd counts use the number of people, never the sum of their wagers.
- The correct answer, author identity, hidden roles, and pending commitments are not exposed prematurely.
- Worked examples A, B, and C produce the stated outcomes.
- Ties and incomplete rounds use an explicit selected policy rather than accidental implementation behavior.
- Score changes are applied once per completed round, and the final winner is determined after all N rounds.

