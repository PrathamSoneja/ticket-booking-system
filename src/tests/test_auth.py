from datetime import UTC, datetime, timedelta

from ticket_booking.auth import Auth


def test_login_logout() -> None:
    auth = Auth({"ada": "correct-horse"})
    assert auth.login("ada", "wrong").status == "AUTH_FAILED"
    result = auth.login("ada", "correct-horse")
    assert result.status == "OK"
    assert auth.token_user(result.token) == "ada"
    assert auth.logout(result.token) is True
    assert auth.token_user(result.token) is None


def test_expired_token() -> None:
    auth = Auth({"ada": "correct-horse"}, lifetime=timedelta(seconds=1))
    issued = datetime(2026, 1, 1, tzinfo=UTC)
    token = auth.login("ada", "correct-horse", now=issued).token
    assert auth.token_user(token, now=issued + timedelta(seconds=2)) is None


def test_signup_rules() -> None:
    auth = Auth({"ada": "correct-horse"})
    weak = auth.signup("charlie", "short")
    assert weak.status == "WEAK_PASSWORD"
    letters = auth.signup("charlie", "onlyletters")
    assert letters.status == "WEAK_PASSWORD"
    clash = auth.signup("ADA", "GoodPass1")
    assert clash.status == "USERNAME_TAKEN"
    ok = auth.signup("charlie", "GoodPass1")
    assert ok.status == "OK"
    assert auth.token_user(ok.token) == "charlie"
    again = auth.signup("Charlie", "OtherPass2")
    assert again.status == "USERNAME_TAKEN"
    login = auth.login("charlie", "GoodPass1")
    assert login.status == "OK"
