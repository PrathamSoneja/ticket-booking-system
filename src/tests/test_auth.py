from datetime import UTC, datetime, timedelta

from ticket_booking.auth import UserLoginManager


def test_login_logout_and_invalid_credentials() -> None:
    auth = UserLoginManager({"ada": "correct-horse"})
    assert auth.login("ada", "wrong").status_code == "AUTH_FAILED"
    result = auth.login("ada", "correct-horse")
    assert result.status_code == "OK"
    assert auth.user_for_token(result.session_token) == "ada"
    assert auth.logout(result.session_token) is True
    assert auth.user_for_token(result.session_token) is None


def test_expired_token_is_rejected() -> None:
    auth = UserLoginManager({"ada": "correct-horse"}, session_valid_time=timedelta(seconds=1))
    issued_at = datetime(2026, 1, 1, tzinfo=UTC)
    token = auth.login("ada", "correct-horse", right_now=issued_at).session_token
    assert auth.user_for_token(token, right_now=issued_at + timedelta(seconds=2)) is None


def test_signup_unique_and_strong_password() -> None:
    auth = UserLoginManager({"ada": "correct-horse"})
    weak = auth.signup("charlie", "short")
    assert weak.status_code == "WEAK_PASSWORD"
    letters = auth.signup("charlie", "onlyletters")
    assert letters.status_code == "WEAK_PASSWORD"
    clash = auth.signup("ADA", "GoodPass1")
    assert clash.status_code == "USERNAME_TAKEN"
    ok = auth.signup("charlie", "GoodPass1")
    assert ok.status_code == "OK"
    assert auth.user_for_token(ok.session_token) == "charlie"
    again = auth.signup("Charlie", "OtherPass2")
    assert again.status_code == "USERNAME_TAKEN"
    login = auth.login("charlie", "GoodPass1")
    assert login.status_code == "OK"
