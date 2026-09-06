# Rejoining and duel repair review — 6 September 2026

The review concentrated on the browser shell, WebSocket lifecycle, duel room
state, shared duel loop, all four duel renderers, and their layouts and SVGs.
The existing engine, game, protocol, and renderer tests were also run.

## Confirmed problems repaired

| Problem | Repair |
| --- | --- |
| Refreshing a match tab could load the player last saved by another tab. Leaving one tab could erase another player's recovery identity. | Keep a tab-specific session alongside durable browser recovery; clear the durable copy only when it belongs to the exiting player. |
| A demo invite opened in the same browser could take the creator's seat. | Bare room links join the free seat or spectate. Each seated page gets its own seat URL for refresh and recovery. |
| Rejoining while a transport retry was pending could create another competing socket. Old callbacks could affect a newer connection. | Cancel pending retries, close the old socket, and ignore callbacks from replaced sockets. |
| Replacing a server socket could briefly mark the seat disconnected. | Register the replacement before awaiting the old socket's closure. |
| Demo clocks started before players had read the rules; one player could force a rematch. | Both players explicitly ready up. Both must also request a rematch. Disconnecting withdraws readiness. |
| Delayed timer callbacks could allow moves after a round's deadline. | Check the deadline when accepting a move, for both match and room duels. |
| Rejected moves and reconnects could leave optimistic controls locked. The first unrelated snapshot could also reset a pending first move. | Rebuild duel controls from fresh server state after an error/reconnect; initialize renderer round tracking on mount. |
| Crown Duel counted strategy and combat as separate rounds in the shell. Reveal headings could advance ahead of the result. | Display the game round and the round/lot just resolved. |
| Bid War displayed the next prize beside the previous lot's bids. | Keep the sold/tied prize on the block during reveal, and identify the upcoming lot separately. |
| Dark duel panels inherited light-theme text and controls. Some RPS color accents lost to CSS specificity. | Scope the dark palette, improve contrast, and fix card accent selectors. |
| Crown Duel's reference panels crowded the board, especially on phones. | Size the layout to its actual container and place mobile play controls before reference rules. |
| Crown portraits were hard to distinguish at card size. | Redraw all five SVG portraits with distinct clothing and equipment; reduce RPS glow and connect the scissor handles. |

## Validation

- Full pytest suite: **1,488 passed**.
- Live Chrome checks: separate seats in the same browser; both-player readiness;
  complete RPS duel and mutual rematch; Crown strategy-to-combat transition;
  Number Clash move secrecy, scoring, and refresh; Bid War bidding and reveal.
- Browser layout checks for all four duels at 320px and 390px; no horizontal
  overflow or JavaScript errors in those checks.
- Additional match check: two tabs sharing browser storage retain different
  player identities after refresh.

## Remaining boundaries

Matches and rooms are in memory. A process restart or eviction deletes them;
a rejoin code restores a seat only while its match still exists. The recovery
message now makes that limitation explicit. Durable recovery would require
persisting game state and restoring timers.

A seat URL intentionally reclaims that seat. Sharing the address bar instead
of the room's Copy link still transfers control; the waiting room explains
which link to share. A bare invite opened after both seats are claimed watches.

Once a duel starts, disconnecting does not pause its clock. That existing rule
prevents a losing player from freezing play by disconnecting. These changes
add preparation and rematch consent without changing that rule.

The review does not establish that every puzzle is free of gameplay or balance
issues. Existing coverage passed, but the live browser checks focused on the
reported rejoining and duel paths.
