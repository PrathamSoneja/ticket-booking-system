from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import pbkdf2_hmac
from hmac import compare_digest
from secrets import token_urlsafe
from threading import Lock


@dataclass(frozen=True)
class LoginResultData:
    status_code: str
    session_token: str = ""
    info_msg: str = ""


@dataclass(frozen=True)
class ActiveUserSession:
    who_logged_in: str
    session_dies_at: datetime


class UserLoginManager:
    SALT_VALUE = b"ticket-booking-demo-v1"

    def __init__(self, user_pass_dict=None, session_valid_time=timedelta(hours=1)):
        if user_pass_dict == None:
            user_pass_dict = {"alice": "wonderland", "bob": "builder"}
            n = 1
            while n <= 32:
                user_pass_dict["user" + str(n)] = "pass" + str(n)
                n = n + 1

        self.saved_password_hashes = {}
        for uname in user_pass_dict:
            raw_pw = user_pass_dict[uname]
            hashed = pbkdf2_hmac("sha256", raw_pw.encode(), self.SALT_VALUE, 200_000)
            self.saved_password_hashes[uname] = hashed

        self.all_sessions = {}
        self.session_valid_time = session_valid_time
        self.big_lock = Lock()

    def login(self, uname, pw, right_now=None):
        stored_hash = self.saved_password_hashes.get(uname)
        pw_hash_check = pbkdf2_hmac("sha256", pw.encode(), self.SALT_VALUE, 200_000)

        if stored_hash is None:
            return LoginResultData("AUTH_FAILED", info_msg="Invalid username or password.")
        if not compare_digest(stored_hash, pw_hash_check):
            return LoginResultData("AUTH_FAILED", info_msg="Invalid username or password.")

        if right_now == None:
            right_now = datetime.now(UTC)

        new_tok = token_urlsafe(32)
        self.big_lock.acquire()
        self.all_sessions[new_tok] = ActiveUserSession(uname, right_now + self.session_valid_time)
        self.big_lock.release()

        return LoginResultData("OK", new_tok, "Logged in.")

    def signup(self, uname, pw, right_now=None):
        name = str(uname).strip()
        if len(name) < 3 or len(name) > 20:
            return LoginResultData("INVALID_REQUEST", info_msg="Username must be 3 to 20 characters.")
        i = 0
        bad = False
        while i < len(name):
            ch = name[i]
            if not (ch.isalnum() or ch == "_"):
                bad = True
            i = i + 1
        if bad:
            return LoginResultData("INVALID_REQUEST", info_msg="Username can only use letters, numbers, and underscore.")
        if len(pw) < 8:
            return LoginResultData("WEAK_PASSWORD", info_msg="Password must be at least 8 characters.")
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
            return LoginResultData("WEAK_PASSWORD", info_msg="Password must include letters and numbers.")

        self.big_lock.acquire()
        taken = False
        for k in self.saved_password_hashes:
            if k.lower() == name.lower():
                taken = True
        if taken:
            self.big_lock.release()
            return LoginResultData("USERNAME_TAKEN", info_msg="That username is already registered.")
        hashed = pbkdf2_hmac("sha256", pw.encode(), self.SALT_VALUE, 200_000)
        self.saved_password_hashes[name] = hashed
        self.big_lock.release()
        return self.login(name, pw, right_now)

    def logout(self, tok):
        self.big_lock.acquire()
        found_something = self.all_sessions.pop(tok, None) is not None
        self.big_lock.release()
        return found_something

    def user_for_token(self, tok, right_now=None):
        self.big_lock.acquire()
        try:
            sess = self.all_sessions.get(tok)
            if sess is None:
                return None

            time_to_check = right_now
            if time_to_check == None:
                time_to_check = datetime.now(UTC)

            if sess.session_dies_at <= time_to_check:
                self.all_sessions.pop(tok, None)
                return None

            return sess.who_logged_in
        finally:
            self.big_lock.release()
