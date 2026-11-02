import sqlite3
from pathlib import Path
from threading import Lock


class RaftStorage:
    def __init__(self, path):
        file = Path(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(file, check_same_thread=False, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS log ("
            "idx INTEGER PRIMARY KEY, term INTEGER NOT NULL, command TEXT NOT NULL, "
            "payload BLOB NOT NULL, request_id TEXT NOT NULL)"
        )
        self.db.executemany(
            "INSERT OR IGNORE INTO state VALUES (?, ?)",
            [("term", "0"), ("voted_for", ""), ("commit_index", "0")],
        )
        self.db.commit()
        self.lock = Lock()

    def _value(self, key):
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row[0]

    @property
    def term(self):
        with self.lock:
            return int(self._value("term"))

    @property
    def voted_for(self):
        with self.lock:
            return self._value("voted_for") or None

    @property
    def commit_index(self):
        with self.lock:
            return int(self._value("commit_index"))

    @property
    def last_index(self):
        with self.lock:
            row = self.db.execute("SELECT COALESCE(MAX(idx), 0) FROM log").fetchone()
            return row[0]

    def term_at(self, idx):
        if idx == 0:
            return 0
        with self.lock:
            row = self.db.execute("SELECT term FROM log WHERE idx=?", (idx,)).fetchone()
            if row is None:
                return None
            return row[0]

    def entry_at(self, idx):
        with self.lock:
            row = self.db.execute(
                "SELECT idx, term, command, payload, request_id FROM log WHERE idx=?", (idx,)
            ).fetchone()
            return row

    def entries_from(self, idx):
        with self.lock:
            rows = self.db.execute(
                "SELECT idx, term, command, payload, request_id FROM log WHERE idx>=? ORDER BY idx", (idx,)
            ).fetchall()
            return rows

    def entries_to(self, idx):
        with self.lock:
            rows = self.db.execute(
                "SELECT idx, term, command, payload, request_id FROM log WHERE idx<=? ORDER BY idx", (idx,)
            ).fetchall()
            return rows

    def set_state(self, term, vote):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute("UPDATE state SET value=? WHERE key='term'", (str(term),))
            self.db.execute("UPDATE state SET value=? WHERE key='voted_for'", (vote or "",))
            self.db.commit()

    def set_commit(self, idx):
        with self.lock:
            self.db.execute("UPDATE state SET value=? WHERE key='commit_index'", (str(idx),))
            self.db.commit()

    def add_entry(self, idx, term, command, payload, req_id):
        with self.lock:
            self.db.execute(
                "INSERT INTO log VALUES (?, ?, ?, ?, ?)", (idx, term, command, payload, req_id)
            )
            self.db.commit()

    def truncate(self, idx):
        with self.lock:
            self.db.execute("DELETE FROM log WHERE idx>=?", (idx,))
            self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()
