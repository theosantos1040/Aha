"""Testes da lógica pura: calc, jogos, economia, utils."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import games
import utils


def test_calc():
    assert utils.safe_calc("2+2") == 4
    assert utils.safe_calc("2+2*5") == 12
    assert utils.safe_calc("(1+2)*3") == 9
    assert utils.safe_calc("10/4") == 2.5
    assert utils.safe_calc("2^10") == 1024
    assert utils.safe_calc("3x4") == 12
    try:
        utils.safe_calc("__import__('os')")
        assert False, "deveria ter falhado"
    except Exception:
        pass
    print("✓ calc")


def test_duration():
    assert utils.parse_duration("10m") == 600
    assert utils.parse_duration("1h") == 3600
    assert utils.parse_duration("2d") == 172800
    assert utils.parse_duration("1h30m") == 5400
    assert utils.parse_duration("5") == 300
    print("✓ duration")


def test_uptime_bar():
    assert "1m" in utils.human_uptime(60)
    assert "1h" in utils.human_uptime(3600)
    assert len(utils.progress_bar(5, 10)) == 10
    print("✓ uptime/bar")


def test_games():
    assert games.coinflip() in ("🪙 Cara", "🪙 Coroa")
    assert games.jokenpo("pedra") is not None
    assert games.jokenpo("invalido") is None
    rolls, total = games.roll(6, 3)
    assert len(rolls) == 3 and sum(rolls) == total
    assert all(1 <= r <= 6 for r in rolls)
    pct, _ = games.ship("ana", "bia")
    assert 0 <= pct <= 100
    assert games.ship("ana", "bia") == games.ship("bia", "ana")  # determinístico
    assert isinstance(games.eightball(), str)
    assert isinstance(games.russian_roulette(), bool)
    print("✓ games básicos")


def test_hangman():
    word = "python"
    guessed = set("python")
    assert games.hangman_won(word, guessed)
    assert not games.hangman_won(word, set("pyth"))
    assert games.hangman_display("abc", {"a"}) == "a _ _"
    print("✓ hangman")


def test_tictactoe():
    b = ["❌", "❌", "❌", " ", " ", " ", " ", " ", " "]
    assert games.ttt_winner(b) == "❌"
    b2 = ["❌", "⭕", "❌", "⭕", "❌", "⭕", "⭕", "❌", "⭕"]
    assert games.ttt_winner(b2) == "draw"
    assert games.ttt_winner([" "] * 9) is None
    print("✓ tictactoe")


def test_database():
    import config
    config.DATA_DB = "/tmp/test_thzyx.sqlite3"
    if os.path.exists(config.DATA_DB):
        os.remove(config.DATA_DB)
    import importlib
    import database
    importlib.reload(database)
    database.init("/tmp/test_thzyx.sqlite3")

    u = "5511999@s.whatsapp.net"
    assert database.get_balance(u) == config.START_BALANCE
    database.add_balance(u, 500)
    assert database.get_balance(u) == config.START_BALANCE + 500

    ok, val = database.claim_daily(u)
    assert ok and val == config.DAILY_REWARD
    ok2, _ = database.claim_daily(u)
    assert not ok2  # já resgatou

    database.add_xp(u, 250)
    lvl, _, _ = database.level_from_xp(database.get_xp(u))
    assert lvl >= 1

    n = database.add_warn("g@g", u, "spam", "admin")
    assert n == 1
    assert database.get_warns_count("g@g", u) == 1

    v = "5511888@s.whatsapp.net"
    ok, _ = database.transfer(u, v, 100)
    assert ok
    database.set_prefix("g@g", "!")
    assert database.get_prefix("g@g") == "!"
    print("✓ database")


if __name__ == "__main__":
    test_calc()
    test_duration()
    test_uptime_bar()
    test_games()
    test_hangman()
    test_tictactoe()
    test_database()
    print("\n✅ TODOS OS TESTES DE LÓGICA PASSARAM")
