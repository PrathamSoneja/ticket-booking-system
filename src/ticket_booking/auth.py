from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import pbkdf2_hmac, sha256
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
        if not self.check_credentials(uname, pw):
            return AuthResult("AUTH_FAILED", message="Invalid username or password.")
        return self.new_session(uname, now)

    def check_credentials(self, uname, pw):
        stored = self.hashes.get(uname)
        check = pbkdf2_hmac("sha256", pw.encode(), self.SALT, 200_000)
        if stored is None:
            return False
        return compare_digest(stored, check)

    def new_session(self, uname, now=None):
        if now == None:
            now = datetime.now(UTC)
        tok = token_urlsafe(32)
        self.lock.acquire()
        self.sessions[self.token_key(tok)] = Session(uname, now + self.lifetime)
        self.lock.release()
        return AuthResult("OK", tok, "Logged in.")

    def signup(self, uname, pw, now=None):
        status, name, digest, msg = self.prepare_signup(uname, pw)
        if status != "OK":
            return AuthResult(status, message=msg)
        self.lock.acquire()
        for existing in self.hashes:
            if existing.lower() == name.lower():
                self.lock.release()
                return AuthResult("USERNAME_TAKEN", message="That username is already registered.")
        self.hashes[name] = digest
        self.lock.release()
        return self.new_session(name, now)

    def prepare_signup(self, uname, pw):
        name = str(uname).strip()
        if len(name) < 3 or len(name) > 20:
            return "INVALID_REQUEST", "", b"", "Username must be 3 to 20 characters."
        i = 0
        bad = False
        while i < len(name):
            ch = name[i]
            if not (ch.isalnum() or ch == "_"):
                bad = True
            i = i + 1
        if bad:
            return (
                "INVALID_REQUEST",
                "",
                b"",
                "Username can only use letters, numbers, and underscore.",
            )
        if len(pw) < 8:
            return "WEAK_PASSWORD", "", b"", "Password must be at least 8 characters."
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
            return "WEAK_PASSWORD", "", b"", "Password must include letters and numbers."
        self.lock.acquire()
        taken = False
        for k in self.hashes:
            if k.lower() == name.lower():
                taken = True
        self.lock.release()
        if taken:
            return "USERNAME_TAKEN", "", b"", "That username is already registered."
        digest = pbkdf2_hmac("sha256", pw.encode(), self.SALT, 200_000)
        return "OK", name, digest, ""

    def add_user_hash(self, uname, digest):
        self.lock.acquire()
        try:
            for name in self.hashes:
                if name.lower() == uname.lower():
                    return compare_digest(self.hashes[name], digest)
            self.hashes[uname] = digest
            return True
        finally:
            self.lock.release()

    @staticmethod
    def token_key(tok):
        return sha256(tok.encode()).hexdigest()

    def add_session_hash(self, key, uname, expiry):
        if isinstance(expiry, str):
            expiry = datetime.fromisoformat(expiry)
        self.lock.acquire()
        try:
            self.sessions[key] = Session(uname, expiry)
        finally:
            self.lock.release()

    def revoke_hash(self, key):
        self.lock.acquire()
        try:
            return self.sessions.pop(key, None) is not None
        finally:
            self.lock.release()

    def logout(self, tok):
        self.lock.acquire()
        found = self.sessions.pop(self.token_key(tok), None) is not None
        self.lock.release()
        return found

    def token_user(self, tok, now=None):
        self.lock.acquire()
        try:
            key = self.token_key(tok)
            sess = self.sessions.get(key)
            if sess is None:
                return None
            if now == None:
                now = datetime.now(UTC)
            if sess.expiry <= now:
                self.sessions.pop(key, None)
                return None
            return sess.user
        finally:
            self.lock.release()
