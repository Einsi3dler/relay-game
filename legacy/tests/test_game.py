from backend.game import RelayGameEngine


def answer(puzzle):
    return puzzle.answer


def test_team_does_not_advance_until_all_active_players_pass():
    engine = RelayGameEngine()
    match = engine.create_match()
    p1 = engine.join_match(match, "One", "alpha", "Terminal")
    p2 = engine.join_match(match, "Two", "alpha", "Oracle")

    result = engine.submit_puzzle(match, p1.id, p1.current_puzzle.id, answer(p1.current_puzzle))

    assert result.ok
    assert match.teams["alpha"].level == 1
    assert match.players[p1.id].status == "grinding"
    assert match.players[p2.id].status == "active"


def test_team_advances_when_all_active_players_pass():
    engine = RelayGameEngine()
    match = engine.create_match()
    p1 = engine.join_match(match, "One", "alpha", "Terminal")
    p2 = engine.join_match(match, "Two", "alpha", "Oracle")

    engine.submit_puzzle(match, p1.id, p1.current_puzzle.id, answer(p1.current_puzzle))
    result = engine.submit_puzzle(match, p2.id, p2.current_puzzle.id, answer(p2.current_puzzle))

    assert result.ok
    assert match.teams["alpha"].level == 2
    assert match.players[p1.id].status == "active"
    assert match.players[p2.id].status == "active"
    assert match.players[p1.id].current_puzzle.level == 2


def test_grind_adds_team_points():
    engine = RelayGameEngine()
    match = engine.create_match()
    player = engine.join_match(match, "Grinder", "alpha", "Quant")
    blocker = engine.join_match(match, "Blocker", "alpha", "Oracle")
    engine.submit_puzzle(match, player.id, player.current_puzzle.id, answer(player.current_puzzle))
    grind = match.players[player.id].current_grind

    result = engine.submit_grind(match, player.id, grind.id, answer(grind))

    assert result.ok
    assert match.teams["alpha"].points == result.payload["reward"]
    assert match.players[player.id].current_grind.id != grind.id
    assert match.players[blocker.id].status == "active"


def test_powerup_purchase_and_insufficient_points():
    engine = RelayGameEngine()
    match = engine.create_match()
    player = engine.join_match(match, "Buyer", "alpha", "Saboteur")

    failed = engine.buy_powerup(match, player.id, "blur")
    assert not failed.ok

    match.teams["alpha"].points = 40
    bought = engine.buy_powerup(match, player.id, "blur")

    assert bought.ok
    assert match.teams["alpha"].points == 0
    assert match.teams["alpha"].inventory["blur"] == 1


def test_shield_blocks_sabotage():
    engine = RelayGameEngine()
    match = engine.create_match()
    attacker = engine.join_match(match, "Attack", "alpha", "Saboteur")
    defender = engine.join_match(match, "Guard", "bravo", "Warden")
    match.teams["alpha"].inventory["shake"] = 1
    match.teams["bravo"].points = 30
    engine.buy_powerup(match, defender.id, "shield")
    engine.activate_shield(match, defender.id)

    result = engine.deploy_powerup(match, attacker.id, "shake", "bravo")

    assert result.ok
    assert result.payload["blocked"] is True
    assert match.teams["bravo"].shield_charges == 0


def test_disconnect_and_reconnect_backlog_flow():
    engine = RelayGameEngine()
    match = engine.create_match()
    player = engine.join_match(match, "Node", "alpha", "Vault")

    disconnected = engine.disconnect_player(match, player.id)
    assert disconnected.ok
    assert match.players[player.id].status == "dormant"
    assert match.teams["alpha"].public(match.players)["difficulty_multiplier"] == 1.2

    reconnected = engine.reconnect_player(match, player.id)
    backlog = match.players[player.id].backlog_puzzle

    assert reconnected.ok
    assert match.players[player.id].status == "backlog"
    cleared = engine.submit_puzzle(match, player.id, backlog.id, answer(backlog))
    assert cleared.ok
    assert match.players[player.id].status == "active"
    assert match.players[player.id].current_puzzle is not None
