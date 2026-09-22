from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import pbkdf2_hmac
from hmac import compare_digest
from secrets import token_urlsafe
from threading import Lock


@dataclass(frozen=True)
class AuthResult:
    status: str
    token: str = ""
    message: str = ""


@dataclass(frozen=True)
class Session:
    user: str
    expiry: datetime


class Auth:
    SALT = b"ticket-booking-demo-v1"

    def __init__(self, users=None, lifetime=timedelta(hours=1)):
        if users == None:
            users = {"alice": "wonderland", "bob": "builder"}
            n = 1
            while n <= 32:
                users["user" + str(n)] = "pass" + str(n)
                n = n + 1
        self.hashes = {}
        for uname in users:
            self.hashes[uname] = pbkdf2_hmac("sha256", users[uname].encode(), self.SALT, 200_000)
        self.sessions = {}
        self.lifetime = lifetime
        self.lock = Lock()

    def login(self, uname, pw, now=None):
        stored = self.hashes.get(uname)
        check = pbkdf2_hmac("sha256", pw.encode(), self.SALT, 200_000)
        if stored is None:
            return AuthResult("AUTH_FAILED", message="Invalid username or password.")
        if not compare_digest(stored, check):
            return AuthResult("AUTH_FAILED", message="Invalid username or password.")
        if now == None:
            now = datetime.now(UTC)
        tok = token_urlsafe(32)
        self.lock.acquire()
        self.sessions[tok] = Session(uname, now + self.lifetime)
        self.lock.release()
        return AuthResult("OK", tok, "Logged in.")

    def signup(self, uname, pw, now=None):
        name = str(uname).strip()
        if len(name) < 3 or len(name) > 20:
            return AuthResult("INVALID_REQUEST", message="Username must be 3 to 20 characters.")
        i = 0
        bad = False
        while i < len(name):
            ch = name[i]
            if not (ch.isalnum() or ch == "_"):
                bad = True
            i = i + 1
        if bad:
            return AuthResult("INVALID_REQUEST", message="Username can only use letters, numbers, and underscore.")
        if len(pw) < 8:
            return AuthResult("WEAK_PASSWORD", message="Password must be at least 8 characters.")
        has_letter = False
        has_digit = False
        j = 0
        while j < len(pw):
            if pw[j].isalpha():
                has_letter = True
            if pw[j].isdigit():
                has_digit = True
            j = j + 1
        if not has_letter or not has_digit:
            return AuthResult("WEAK_PASSWORD", message="Password must include letters and numbers.")
        self.lock.acquire()
        taken = False
        for k in self.hashes:
            if k.lower() == name.lower():
                taken = True
        if taken:
            self.lock.release()
            return AuthResult("USERNAME_TAKEN", message="That username is already registered.")
        self.hashes[name] = pbkdf2_hmac("sha256", pw.encode(), self.SALT, 200_000)
        self.lock.release()
        return self.login(name, pw, now)

    def logout(self, tok):
        self.lock.acquire()
        found = self.sessions.pop(tok, None) is not None
        self.lock.release()
        return found

    def token_user(self, tok, now=None):
        self.lock.acquire()
        try:
            sess = self.sessions.get(tok)
            if sess is None:
                return None
            if now == None:
                now = datetime.now(UTC)
            if sess.expiry <= now:
                self.sessions.pop(tok, None)
                return None
            return sess.user
        finally:
            self.lock.release()
