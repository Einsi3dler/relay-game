# Bullshit Quiz perks

Status: **design, accepted by the owner on 23 October 2026**. Not built yet.
Card text and point values are provisional until a playtest.

This document says what each perk does: who may play it, when, who sees it,
and exactly how it changes scores. It does not yet say how players get perks,
how many they hold, or what the screens look like. That is the next step, and
section 6 lists what it has to settle. The base game is
[BULLSHIT_QUIZ.md](BULLSHIT_QUIZ.md); "§7" and the like refer to its sections.

The 30 perks are the owner's list. Where this draft adds a rule the list did
not state, or changes one, section 7 says so.

## 0. Decisions settled by the owner (23 October 2026)

| Question | Decision |
| --- | --- |
| How many perks a round | **No cap.** Any player may play any perk whenever its own rules allow, several in one round included. Section 5 says how they stack. |
| When guessers lock | **After the talking.** The Discussion runs on a timer and the ballot stays shut until it ends, so the floor perks reach everybody. Blind Faith is the only way to lock earlier. For this one step it replaces the base game's "no timers". |
| The rules section 7 adds to the list | **Accepted.** |

## 1. Ground rules for every perk

1. **Single use.** Playing a perk spends it. A perk cancelled by Objection! or
   redirected by Not My Problem is still spent. A perk the server refuses (a
   second Kingmaker in the same round, say) is not.
2. **No cap on perks.** Any player may play any perk its rules allow, and
   several in one round. The one limit across perks: a player plays the same
   perk at most once a round, a Counterfeit copy included, so two Double or
   Nothings do not make four times. Section 5 says how the rest stack.
3. **No perk gives away the truth or a role early.** The truthful player has no
   wager, so a perk only a wager holder can play (Insurance Fraud, Double or
   Nothing) would out a speaker as a bullshitter if it were announced. Those
   stay secret. The server sends a secret perk's details to its owner only,
   and to the host only where this document says so. §9 applies to perks too:
   hiding something on screen is not the same as not sending it.
4. **Everything comes out at the reveal.** The reveal lists every perk played
   in the round, who played it, on whom, and the points it moved, each as its
   own part of the player's line (§12: "fooled 3, lost an 8 wager, Silver
   Tongue +3").
5. **If the round does not happen, nothing is spent.** When a round is called
   off in the briefing (its author left), every perk played in it goes back to
   its owner, as wagers already do. So does a perk whose target leaves the
   game before it takes effect.
6. **A nomination on somebody who did not vote pays nothing.** A nominated
   guesser who casts no vote (offline, or the host's **Lock anyway**) makes the
   nomination worth zero either way.
7. **Speaking perks happen on the call.** The app announces them and shows a
   20-second countdown on every screen; the host runs and polices them. The
   app does not listen to the call. Apart from the discussion timer opening
   the ballot, the host still paces every step.
8. **Scores may go negative**, as in the base game. No perk adds a floor.

## 2. Words used below

**Windows**, in round order:

| Window | Room phase | What is happening |
| --- | --- | --- |
| Briefing | `briefing` | Speakers have their roles, bullshitters lock their wagers, the truthful player confirms. Guessers can see the question and options. |
| Discussion | `open`, timer running | The talking on the call, on a timer. The ballot is shut, so guessers cannot lock yet. Floor perks happen here. |
| Ballot | `open`, timer ended | Guessers lock an answer and a wager. |
| Headcount | `votes` | Votes are locked and the count per option is on screen. The truth is still hidden. |
| Reveal | `reveal` | Truth, roles and scores. Lasts until the host presses **Next round**. |

"Before the votes lock" means any time in the Briefing, Discussion or
Ballot, until the host presses **Lock the votes**.

**Seen by.** *Secret*: the owner only (and the host, where noted) until the
reveal. *Public*: on every screen the moment it is played.

**Speaker**: the truthful player or a bullshitter. **Anyone**: speakers and
guessers.

**Plain score**: a player's round score from the §7 formulas alone (the
truthful reward; people fooled plus or minus the wager; or plus or minus the
wager), using the round's crowd winner, with no personal perk applied.

**Main answer**: the one answer a guesser locks, and the only one that counts
as a vote. A Backup Plan answer never does.

**Crowd winner**: §7 with the owner's tie rule (a tie at the top means
nobody wins), except that Kingmaker can settle a tie.

**Outright first**: more votes than every other option. Heads only: a
Kingmaker pick wins the crowd but is not an outright first.

**Outright second**: at least one vote, exactly one option with more, and
every other option with fewer.

## 3. At a glance

| Perk | Who | When | Seen by | In short |
| --- | --- | --- | --- | --- |
| Insurance Fraud | guesser, bullshitter | before locking your wager | secret | a lost wager costs half, rounded up |
| Double or Nothing | guesser, bullshitter | before locking your wager | secret | wager counts twice, win or lose |
| Backup Plan | guesser | as you lock | secret | main wrong, backup right: lose nothing |
| Blind Faith | guesser | Briefing | secret | lock before the talking; right pays 3w |
| Damage Control | guesser, bullshitter | Reveal | public | win back up to 5 of a lost wager |
| Ride or Die | guesser | before the votes lock | secret | +5 or −5 on another guesser being right |
| I Know Your Judgement Is Terrible | guesser | before the votes lock | secret | +5 or −5 on another guesser being wrong |
| Lone Wolf | guesser | before the votes lock | secret | +10 for being the only one right |
| I Call Bullshit | guesser | before the votes lock | secret | +5 or −5 on a speaker bluffing |
| Read the Room | guesser | Briefing | secret | vote last, after seeing the counts |
| Inside Information | guesser | Ballot, before you lock | secret | see one locked guesser's answer |
| The Audit | anyone | Ballot | public | show one locked guesser's answer and wager to all |
| Silver Tongue | speaker | before the votes lock | secret | +1 per person on your answer |
| Victory Lap | speaker | before the votes lock | secret | +10 for outright first |
| Target Audience | speaker | Briefing | secret | +5 if your chosen guesser picks you |
| Honourable Mention | speaker | before the votes lock | secret | +5 for outright second |
| False Witness | speaker | Briefing | secret (recruit and host told) | a guesser speaks up for your answer |
| The Hot Seat | anyone | Discussion | public | 20 seconds of your questions to one speaker |
| The Mute Button | anyone | Discussion, after opening statements | public | one speaker silent for 20 seconds |
| The Last Word | anyone | Discussion | public | 20 seconds just before the ballot opens |
| Forbidden Words | anyone | Briefing or Discussion | public | three banned words, −2 per slip |
| Blackmail | anyone | before the target locks | public | target bets their highest number left |
| Blood Pact | guesser | before either of you locks | public | both right: double; either wrong: both lose |
| Pickpocket | anyone | Reveal | public | take 3 from a wrong guesser |
| Kingmaker | anyone | Headcount | public | settle a tie at the top |
| Doppelgänger | guesser | before the votes lock | secret | take another player's plain score |
| Hostile Takeover | guesser | before you lock, not in the last round | secret, announced at the reveal | right answer: swap totals with someone |
| Objection! | anyone | when a floor perk is announced | public | cancel it |
| Not My Problem | the target | when a perk lands on you | like the perk | send it to someone else |
| Counterfeit | as the copy | as the copy | as the copy | play a perk someone else already revealed |

## 4. The perks

The quote under each name is the card text players see. The lines under it
are the rules the server enforces. `w` is the player's wager this round.

### 4.1 Wager perks

These change your own wager.

#### Insurance Fraud · `insurance_fraud`

> Lose your wager this round and only half of it comes off, rounded up. Win
> it and you get the full amount.

- **Who:** anyone with a wager this round: a guesser or a bullshitter.
- **When:** before you lock your wager. For a bullshitter that is the
  Briefing; for a guesser, any time until they lock.
- **Seen by:** secret.
- **Effect:** a lost wager costs ⌈L / 2⌉, where L is the loss after any
  multiplier. Losing 7 costs 4; losing 8 costs 4. A won wager is untouched,
  and so are a bullshitter's points per person fooled.

#### Double or Nothing · `double_or_nothing`

> Your wager counts twice this round, win or lose. Bet 8 and it is +16 or
> −16.

- **Who:** guesser or bullshitter.
- **When:** before you lock your wager.
- **Seen by:** secret.
- **Effect:** the wager part of your score is doubled, gain or loss. A
  bullshitter's points per person fooled are not doubled. You still spend one
  wager number.

#### Backup Plan · `backup_plan`

> Pick a backup answer as well as your main one. If your main answer is wrong
> and your backup is right, you lose nothing.

- **Who:** guesser, in a round with at least three options.
- **When:** as you lock your answer.
- **Seen by:** secret.
- **Effect:** if the main answer is wrong and the backup is right, the wager
  part is 0 (not a gain). Otherwise the backup changes nothing.
- The backup must differ from the main answer. It is not a vote, and no
  other perk that looks at your answer (Lone Wolf, Ride or Die, Blood Pact,
  Pickpocket, The Audit, Inside Information) ever sees it.

#### Blind Faith · `blind_faith`

> Lock your answer and wager before anyone has said a word. Right pays three
> times your wager. Wrong costs only the wager.

- **Who:** guesser, in a round with at least three options (section 7).
- **When:** in the Briefing. It is the only way a guesser can lock before the
  ballot opens.
- **Seen by:** secret. The guesser's "committed" tick stays hidden until the
  ballot opens, or the early tick would give it away.
- **Effect:** right: +3w. Wrong: −w.
- Once locked it is like any other locked answer: Inside Information and The
  Audit can see it, and Blackmail and Blood Pact can no longer reach it.

#### Damage Control · `damage_control`

> After the reveal, win back up to 5 points of a wager you just lost.

- **Who:** a guesser or bullshitter who lost their wager this round.
- **When:** in the Reveal, before the host presses **Next round**.
- **Seen by:** public (the round is already revealed).
- **Effect:** +min(5, what the lost wager cost you). A bullshitter recovers
  the wager loss only: fooled 3, lost an 8 wager, net −5, becomes net 0.
- Not playable after a won wager, a Backup Plan rescue (nothing was lost), or
  without a wager (the truthful player, a guesser who cast no vote).

### 4.2 Side bets

Extra points on top of your normal score. All are secret until the reveal,
and the nominated player is never told.

#### Ride or Die · `ride_or_die`

> Secretly back another guesser. If they get it right, you get 5. If they get
> it wrong, you lose 5.

- **Who:** guesser. **Target:** another guesser this round.
- **When:** before the votes lock.
- **Effect:** +5 if the target's main answer is right, −5 if it is wrong, 0
  if they cast no vote.

#### I Know Your Judgement Is Terrible · `terrible_judgement`

> Secretly bet against another guesser. If they get it wrong, you get 5. If
> they get it right, you lose 5.

- Ride or Die with the signs flipped. Same target, timing and no-vote rule.

#### Lone Wolf · `lone_wolf`

> If you are the only guesser who picks the right answer, take 10 extra
> points.

- **Who:** guesser, in a round with at least three guessers.
- **When:** before the votes lock. It can be after you lock your own answer.
- **Effect:** +10 if the right option has exactly one vote and it is yours,
  on top of your won wager. Otherwise nothing: it costs nothing to try.

#### I Call Bullshit · `call_bullshit`

> Secretly accuse one speaker. If they are bluffing, you get 5. If they are
> telling the truth, you lose 5.

- **Who:** guesser. **Target:** any speaker this round, including the one
  whose answer you picked.
- **When:** before the votes lock.
- **Effect:** +5 if the target is a bullshitter, −5 if truthful. Independent
  of your answer and wager.

### 4.3 Information

These break the base rule that guesses stay private until the lock (§9), on
purpose and only as far as stated. None of them shows which answer is true.

#### Read the Room · `read_the_room`

> Vote last. Once everybody else has locked in, you see how many picked each
> answer, then choose yours.

- **Who:** guesser.
- **When:** declared in the Briefing.
- **Seen by:** secret.
- **Effect:** you cannot lock until every other awaited guesser has. Then you
  see the anonymous count per option and lock your answer and wager.
- **Several in one round:** they all see the same count (every vote except
  the Read the Room players') and then lock in any order. None sees another's
  vote. Otherwise two of them would each wait for the other forever.
- It needs an extra beat before the lock; see section 6.

#### Inside Information · `inside_information`

> Peek at one guesser's locked answer before you lock yours. They will not
> know.

- **Who:** a guesser who has not locked. **Target:** a guesser who has.
- **When:** Ballot.
- **Seen by:** secret.
- **Effect:** you see the target's main answer, not their wager or any
  backup. Their answer stays locked.

#### The Audit · `the_audit`

> Put one guesser's locked answer and wager on every screen while voting is
> still open.

- **Who:** anyone. **Target:** a guesser who has locked.
- **When:** Ballot.
- **Seen by:** public.
- **Effect:** the target's main answer and wager are shown to the whole room.
  Nothing changes; the room decides what to make of it.

### 4.4 Speaker perks

For the truthful player and bullshitters alike, so playing one does not say
which you are. All secret until the reveal unless noted.

#### Silver Tongue · `silver_tongue`

> Earn 1 extra point for every person who picks your answer. Each still
> counts as one vote.

- **Who:** speaker. **When:** before the votes lock.
- **Effect:** +v, where v is the votes on your option. The truthful player
  normally earns nothing per vote (§7); with this they do. For a bullshitter
  it doubles the points for people fooled. Vote counts do not change.

#### Victory Lap · `victory_lap`

> If your answer draws the biggest crowd outright, take 10 extra points.

- **Who:** speaker. **When:** before the votes lock.
- **Effect:** +10 for outright first. A tie at the top that Kingmaker settles
  is a crowd win but not an outright first, so it does not pay.

#### Target Audience · `target_audience`

> Secretly choose one guesser. If they pick your answer, you get 5 extra
> points.

- **Who:** speaker. **Target:** a guesser.
- **When:** Briefing, so there is somebody to win over during the Discussion.
- **Effect:** +5 if the target's main answer is your option. No loss
  otherwise.

#### Honourable Mention · `honourable_mention`

> If your answer finishes outright second, take 5 extra points.

- **Who:** speaker. **When:** before the votes lock.
- **Effect:** +5 for outright second (section 2). With two options, that
  means losing the crowd without a tie and with at least one vote.

#### False Witness · `false_witness`

> Secretly recruit a guesser to say a few words for your answer. They keep it
> quiet until the reveal and still vote however they like.

- **Who:** speaker. **Target:** a guesser.
- **When:** Briefing.
- **Seen by:** the recruit, privately, with the option they must back; and
  the host, so the host can hold them to it.
- **Effect:** no points. The recruit gives a short statement for your option
  during the Discussion and votes freely. A recruit who stays quiet loses
  nothing but the room's respect.
- The recruit learns which option you defend, which is public anyway. They do
  not learn your role.

### 4.5 Floor perks

These run on the call. They are public the moment they are played, so
Objection! can cancel them and the target can answer with Not My Problem.
They run one at a time in the order played; the host starts each one and
the app shows the countdown.

#### The Hot Seat · `hot_seat`

> Put one speaker on the spot for 20 seconds. You ask, only they answer, and
> nobody can jump in.

- **Who:** anyone. **Target:** a speaker. **When:** Discussion.
- **Effect:** for 20 seconds only the player who played it asks and only the
  target answers. Bullshitters may still lie.

#### The Mute Button · `mute_button`

> Silence one speaker for 20 seconds, once every speaker has made their
> opening case.

- **Who:** anyone. **Target:** a speaker.
- **When:** Discussion, after every speaker has had a first chance to explain.
  The host marks that moment.
- **Effect:** the target may not speak for 20 seconds.

#### The Last Word · `last_word`

> Take 20 seconds, uninterrupted, right before voting opens.

- **Who:** anyone.
- **When:** claimed during the Discussion. It runs when the discussion timer
  ends, before the ballot opens. Several run in the order claimed.
- **Effect:** 20 seconds to defend, endorse or dismantle. Nobody else talks.

#### Forbidden Words · `forbidden_words`

> Ban three words for one speaker. Every time they say one, they lose 2
> points.

- **Who:** anyone. **Target:** a speaker; at most one Forbidden Words per
  speaker per round.
- **When:** Briefing or Discussion. It applies from the announcement until
  the votes lock.
- **Seen by:** public, words included, so the whole room can listen for them.
- **Effect:** −2 per slip, counted by the host with a tally button, capped at
  −10 (section 7).
- Three different single words, none taken from the target's own option
  text: they have to be able to say their answer.

### 4.6 Power plays

#### Blackmail · `blackmail`

> Force one guesser to bet their highest number left. They still choose their
> own answer.

- **Who:** anyone. **Target:** a guesser who has not locked; one Blackmail
  per target per round.
- **When:** before the target locks.
- **Seen by:** public.
- **Effect:** the target's wager is fixed at their highest unused number.
  Because wager numbers are single-use, this also burns that number for the
  rest of the game, which can hurt more than the round itself.

#### Blood Pact · `blood_pact`

> Bind yourself to another guesser in front of everyone. Both right: you each
> win double your own wager. Either wrong: you both lose yours.

- **Who:** guesser. **Target:** another guesser. Neither may have locked, and
  neither may already be in a pact this round.
- **When:** before either of you locks.
- **Seen by:** public. The target does not have to agree; Not My Problem is
  their way out.
- **Effect:** both right: each +2w of their own wager. Otherwise each −w,
  even the one who was right. If either casts no vote, the pact is void and
  both score normally.

#### Pickpocket · `pickpocket`

> After the reveal, take 3 points from a guesser who got it wrong, in front of
> everyone.

- **Who:** anyone. **Target:** a guesser whose main answer was wrong this
  round.
- **When:** Reveal, before **Next round**.
- **Seen by:** public.
- **Effect:** target −3, you +3. Different players may pick the same victim.

#### Kingmaker · `kingmaker`

> When the biggest crowd is a tie, you pick which speaker wins it. The truth
> stays hidden until the reveal.

- **Who:** anyone, including a speaker who is in the tie.
- **When:** Headcount, and only if the top count is tied and above zero.
- **Seen by:** public.
- **Effect:** the chosen speaker wins the crowd for every rule that asks who
  won it: a bullshitter's wager, or the truthful player's N. Guessers are
  still scored on the true answer. Vote counts do not change, so it makes
  nobody outright first or outright second.
- The first Kingmaker played settles it. Any others that round are refused
  and not spent.

#### Doppelgänger · `doppelganger`

> Secretly pick another player. Their score this round, before any perks,
> becomes yours, even if it is negative.

- **Who:** guesser (section 7). **Target:** any other player.
- **When:** before the votes lock.
- **Seen by:** secret.
- **Effect:** your plain score, with any wager perks played on you, is
  replaced by the target's plain score. Theirs is not touched. You still lock
  an answer and spend a wager number as usual; only the points are replaced.
- Plain scores ignore perks, so two Doppelgängers on each other just trade
  plain scores and nothing loops.
- Points perks played on you still apply on top (Pickpocket, for instance).

#### Hostile Takeover · `hostile_takeover`

> Secretly pick another player. If your answer is right, you swap total scores
> with them after this round.

- **Who:** guesser. **Target:** any other player.
- **When:** before you lock, in any round except the last one the game
  plays.
- **Seen by:** secret until the reveal, where it is announced before it
  happens.
- **Effect:** the round is scored as normal first. Then, if your main answer
  was right, your running total and the target's are exchanged, this round's
  points included. If you were wrong, nothing happens beyond your lost wager.
- The target gets a moment at the reveal to answer with Not My Problem.
- Several in one round run in the order they were played.
- The strongest perk on the list by a distance, which is why the last round
  is off limits (section 7).

### 4.7 Counters

#### Objection! · `objection`

> Cancel a speaking perk someone just announced, before it starts.

- **Who:** anyone, for themselves or somebody else.
- **When:** after a floor perk (The Hot Seat, The Mute Button, The Last Word,
  Forbidden Words) is announced and before it starts.
- **Seen by:** public.
- **Effect:** the floor perk does not happen and is still spent. Objection!
  cannot be objected to, and it touches no other kind of perk.

#### Not My Problem · `not_my_problem`

> When a perk lands on you, send it to someone else it could have hit, even
> the player who sent it.

- **Who:** the target of a perk they are told about: The Hot Seat, The Mute
  Button, Forbidden Words, Blackmail, Blood Pact, The Audit, Pickpocket, False
  Witness, and Hostile Takeover (at the reveal).
- **When:** as soon as the perk reaches you, before it takes effect.
- **Seen by:** like the perk it redirects. A redirected False Witness stays
  between the new recruit and the host.
- **Effect:** the perk moves to another player who meets that perk's own
  target rules, which may be the player who played it. If nobody qualifies,
  Not My Problem cannot be played. The original perk is still spent.
- A perk can be redirected once; its new target cannot pass it on.
- Secret perks the target never hears about (Ride or Die, Inside
  Information, Doppelgänger and the rest) cannot be redirected.

#### Counterfeit · `counterfeit`

> Copy any perk another player has already used and revealed this game, and
> play it as your own.

- **Who, when, seen by:** as the copied perk.
- **Effect:** when you play it, name a perk another player used earlier this
  game that has since been revealed (public perks the moment they are played,
  secret ones at their round's reveal). It follows that perk's rules exactly
  and shows as a Counterfeit at the reveal. The original stays spent.
- It cannot copy Counterfeit. It can copy a perk revealed earlier in the same
  round: a second Pickpocket in the same reveal, for instance.
- It counts as the copied perk for every "at most one" rule (one Blackmail
  per target, the first Kingmaker wins, the same perk once a round).

## 5. Scoring order

Points are worked out once the Headcount closes, after any Kingmaker, and
added to totals at the Reveal exactly once (§12). Reveal-time perks follow.

1. **Votes.** Count main answers per option.
2. **Crowd winner.** Outright first; otherwise Kingmaker's pick; otherwise
   nobody.
3. **Plain scores** for every player.
4. **Wager perks**, for each player with a wager, in this order:
   1. Success. A guesser in a Blood Pact succeeds only if both are right.
   2. Multipliers. Double or Nothing ×2 on gain and loss; Blind Faith ×3 on
      gain; Blood Pact ×2 on gain. When several apply, they multiply:
      Double or Nothing with Blind Faith pays six times the wager and loses
      two times.
   3. Insurance Fraud. A loss becomes ⌈loss / 2⌉.
   4. Backup Plan. Main answer wrong, backup right: the wager part is 0.
5. **Doppelgänger** replaces the result of steps 3 and 4 with the target's
   plain score, so your own wager perks do nothing that round.
6. **Side points** add on: Silver Tongue, Victory Lap, Target Audience,
   Honourable Mention, Lone Wolf, Ride or Die, I Know Your Judgement Is
   Terrible, I Call Bullshit, Forbidden Words fines.
7. **Reveal** adds every line to the totals.
8. **Hostile Takeovers** swap totals in the order played, after each target's
   chance to redirect.
9. **During the Reveal**, as they are played: Damage Control, Pickpocket.

### Worked example

Twelve players: wagers run 1 to 11 and the truthful reward is 12 or 6. Three
options and nine guessers.

- **A** is defended by Tara (truthful), **B** by Ben (bluffing, wagered 7),
  **C** by Cleo (bluffing, wagered 5).
- Votes: A 4, B 4, C 1. A tie at the top.
- At the Headcount, Gus plays **Kingmaker** and picks Ben, so Ben wins the
  crowd.

| Player | Role | Answer, wager | Perk | Plain | Perk effect | Round |
| --- | --- | --- | --- | --- | --- | --- |
| Tara | truthful | A | Silver Tongue | +6, crowd lost | +4 for four votes | **+10** |
| Ben | bullshitter | B, 7 | Victory Lap | 4 + 7 = +11 | none: a Kingmaker win, not outright | **+11** |
| Cleo | bullshitter | C, 5 | Insurance Fraud | 1 − 5 = −4 | loss of 5 becomes 3 | **−2** |
| Gus | guesser | A, 3 | Kingmaker | +3 | | **+3** |
| Hana | guesser | B, 6 | Double or Nothing, Insurance Fraud | −6 | doubled to 12, then halved to 6 | **−6** |
| Ivo | guesser | A, 9 | Blood Pact with Jas | +9 | Jas was wrong, so Ivo loses | **−9** |
| Jas | guesser | C, 4 | (in Ivo's pact) | −4 | | **−4** |
| Kim | guesser | B, 2, backup A | Backup Plan | −2 | backup right: 0 | **0** |
| Lou | guesser | A, 8 | Doppelgänger on Ben | +8 | replaced by Ben's plain +11 | **+11** |
| Max | guesser | B, 5 | | −5 | pickpocketed by Pia, −3 | **−8** |
| Ned | guesser | A, 10 | Ride or Die on Ivo | +10 | Ivo was right, +5 | **+15** |
| Pia | guesser | B, 1 | Pickpocket on Max, at the Reveal | −1 | +3 | **+2** |

Max could have answered Pia with Not My Problem and sent the Pickpocket back:
she guessed wrong too, so she qualifies as a target.

## 6. For the system design (the next step)

What these rules lean on that the next step has to decide:

- **How players get perks**, how many they hold, and whether anybody can see
  another player's hand. If everyone can see you hold Not My Problem, nobody
  will target you.
- **Role luck.** Everyone is truthful exactly once, but bluffing turns are
  random and can be zero (§3). A speaker perk is guaranteed one chance per
  game, and Insurance Fraud or Double or Nothing as a bullshitter may never
  get one. A hand of speaker perks can be dead weight.
- **Small games.** With three players every round has two options and one
  guesser, so Backup Plan, Blind Faith, Lone Wolf and every perk aimed at
  "another guesser" can never be played. With four players there are at most
  two guessers.
- **The discussion timer** (section 0). Its length: proposed 60 seconds per
  speaker, so two to four minutes, in `backend/config.py`. Whether the host
  can open the ballot early or add time: proposed both. Floor perks run
  inside the timer rather than stopping it, except The Last Word, which runs
  after it.
- **New beats in a round,** all host-paced: a late vote for Read the Room
  before the lock; a pause at the Headcount for Kingmaker; a short pause
  after each public perk for Objection! and Not My Problem; time in the
  Reveal for Damage Control, Pickpocket and Hostile Takeover redirects; the
  host marking "opening statements done" for The Mute Button; a slip tally
  for Forbidden Words.
- **The host screen is often screen-shared.** False Witness, the one secret
  perk the host must see, belongs in a host-only panel like Round history,
  never on the main host view.
- **Small leaks.** A Blind Faith guesser's committed tick during the
  Briefing (section 4.1). Any "perks in play" counter that a bullshitter's
  secret perk could raise but the truthful player's could not.

## 7. Where this draft changes or adds to the list

Accepted by the owner on 23 October 2026.

| Perk | The list says | This draft | Why |
| --- | --- | --- | --- |
| Blind Faith | any round, three times the wager | rounds with three or four options only, still three times | A blind pick at three times the wager averages +1 wager in a two-option round with no skill involved. With three options it averages +⅓ of a wager; with four it breaks even. |
| Doppelgänger | any player may use it | guessers only | In a two-option round the bullshitter knows the other speaker is truthful, so copying them is a guaranteed N or floor(N / 2). |
| Hostile Takeover | any round | not in the game's last round | It can move more points than the rest of the game put together. In the last round whoever is bottom could take first place with one right answer, and nobody would have a round left to answer it. |
| Every perk | not stated | a player plays the same perk at most once a round | Otherwise two copies of Double or Nothing make four times the wager, and two Lone Wolfs pay 20. |
| Counterfeit | copy a perk when it is revealed | choose which revealed perk to copy when you play it | Same spirit, and no reaction window is needed at the moment of a reveal. |
| Read the Room | everyone else locks first | several players share one late window | Two Read the Room players would otherwise wait on each other forever. |
| Forbidden Words | −2 per slip | −2 per slip, capped at −10; words cannot come from the target's own answer | Keeps it a joke rather than a 30-point swing decided by the host's ears, and the target must be able to say their answer. |
| Lone Wolf | any round | rounds with at least three guessers | With one guesser it pays every time they are right. |
| Honourable Mention | outright second | outright second with at least one vote | Otherwise zero votes in a two-option round would count as second place. |
| I Call Bullshit | before the reveal | before the votes lock | Like every other scoring perk, it closes before the Headcount shows where the crowd went. |
| Blood Pact | before voting | neither may have locked or be in another pact; void if either casts no vote | Keeps a pact to two people and gives the target time to react. |
| Kingmaker | who may play it is not stated | anyone, including a speaker in the tie; the first one played counts | A tied speaker will crown themselves. That seems fine for a perk that needs a tie to work. |
| Objection! | speaking perks | the four floor perks only, and it cannot be objected to | No Objection chains. |
| Not My Problem | any perk that targets you | perks you are told about, once per perk, following that perk's own target rules | You cannot react to a perk you never hear about, and redirects cannot ping-pong. |
| Insurance Fraud, Double or Nothing | visibility not stated | secret | Only a wager holder can play them, so announcing one from a speaker would out a bullshitter. |
| Ride or Die, I Know Your Judgement Is Terrible, I Call Bullshit | "another guesser", "as a guesser" | guessers only, as written | The truthful player always knows the answer, and so does a bullshitter in a two-option round. |
