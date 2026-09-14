from datetime import UTC, datetime, timedelta

from ticket_booking.auth import AuthService


def test_login_logout_and_invalid_credentials() -> None:
    auth = AuthService({"ada": "correct-horse"})
    assert auth.login("ada", "wrong").status == "AUTH_FAILED"
    result = auth.login("ada", "correct-horse")
    assert result.status == "OK"
    assert auth.user_for_token(result.token) == "ada"
    assert auth.logout(result.token) is True
    assert auth.user_for_token(result.token) is None


def test_expired_token_is_rejected() -> None:
    auth = AuthService({"ada": "correct-horse"}, ttl=timedelta(seconds=1))
    issued_at = datetime(2026, 1, 1, tzinfo=UTC)
    token = auth.login("ada", "correct-horse", now=issued_at).token
    assert auth.user_for_token(token, now=issued_at + timedelta(seconds=2)) is None
