import json
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock
from uuid import NAMESPACE_URL, uuid5

from .seed import loaded_shows


def parse_seats(raw):
    picked = []
    for part in str(raw).replace(";", ",").split(","):
        bit = part.strip()
        if bit and bit not in picked:
            picked.append(bit)
    return picked


@dataclass(frozen=True)
class Outcome:
    status: str
    message: str
    booking_id: str = ""
    txn: str = ""
    raft_leader: str = ""
    raft_term: int = 0
    raft_log_index: int = 0
    raft_commit_index: int = 0
    raft_last_applied: int = 0
    raft_committed: bool = False
    request_replayed: bool = False


@dataclass
class Seat:
    seat_id: str
    status: str = "AVAILABLE"
    owner: str = None
    booking: str = None


@dataclass
class Booking:
    booking_id: str
    user_id: str
    show_id: str
    seat_id: str
    status: str = "CONFIRMED"
    txn: str = ""


class SeatMap:
    def __init__(self, catalog=None, capacity=50):
        if capacity < 1:
            raise ValueError("seats_per_show must be positive")
        if catalog:
            self.catalog = catalog
        else:
            self.catalog = loaded_shows
        self.grid = {}
        self.locks = {}
        for show in self.catalog:
            row = {}
            for i in range(1, capacity + 1):
                key = f"A{i}"
                row[key] = Seat(key)
                self.locks[show + "/" + key] = Lock()
            self.grid[show] = row
        self.bookings = {}
        self.seen = {}
        self.seen_meta = {}
        self.lock = Lock()
        self.box = Lock()

    @staticmethod
    def _request_payload(kind, **kwargs):
        payload = {}
        for key, value in kwargs.items():
            if value is not None:
                payload[key] = value
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def _record(self, req_id, result, user_id=None, kind=None, payload=None):
        if req_id is None or req_id == "":
            return
        self.seen[req_id] = result
        self.seen_meta[req_id] = {
            "user_id": user_id,
            "kind": kind,
            "payload": self._request_payload(kind=kind, **(payload or {})),
        }

    def _lookup(self, req_id, user_id=None, kind=None, payload=None):
        if req_id is None or req_id == "":
            return None
        result = self.seen.get(req_id)
        if result is None:
            return None
        meta = self.seen_meta.get(req_id)
        if meta is None:
            return result
        if user_id is not None and meta.get("user_id") not in (None, user_id):
            return Outcome("REQUEST_CONFLICT", "This request ID was already used by a different user.")
        if kind is not None and meta.get("kind") not in (None, kind):
            return Outcome("REQUEST_CONFLICT", "This request ID was already used for a different operation.")
        if payload is not None:
            candidate = self._request_payload(kind=kind, **payload)
            if meta.get("payload") != candidate:
                return Outcome("REQUEST_CONFLICT", "This request ID was already used with a different payload.")
        return result

    def shows(self):
        out = []
        for sid, info in self.catalog.items():
            row = {"show_id": sid}
            row.update(info)
            out.append(row)
        return out

    def seats(self, show_id):
        with self.lock:
            here = self.grid.get(show_id)
            if here is None:
                return None
            out = []
            for s in here.values():
                out.append({"seat_id": s.seat_id, "status": s.status})
            return out

    def analytics(self):
        rows = {}
        for show_id in self.catalog:
            rows[show_id] = {
                "show_id": show_id,
                "booking_count": 0,
                "cancelled_count": 0,
                "cancellation_rate": 0.0,
            }
        self.lock.acquire()
        try:
            for booking in self.bookings.values():
                row = rows.get(booking.show_id)
                if row is None:
                    continue
                row["booking_count"] = row["booking_count"] + 1
                if booking.status == "CANCELLED":
                    row["cancelled_count"] = row["cancelled_count"] + 1
            for row in rows.values():
                if row["booking_count"] > 0:
                    row["cancellation_rate"] = row["cancelled_count"] / row["booking_count"]
            return list(rows.values())
        finally:
            self.lock.release()

    @contextmanager
    def _seat_locks(self, show_id, seats):
        keys = sorted(f"{show_id}/{seat}" for seat in seats)
        with self.box:
            locks = [self.locks.setdefault(k, Lock()) for k in keys]
        for lock in locks:
            lock.acquire()
        try:
            yield
        finally:
            for lock in reversed(locks):
                lock.release()

    def _finish(self, req_id, out, user_id, kind, meta):
        self._record(req_id, out, user_id=user_id, kind=kind, payload=meta)
        return out

    def book(self, user_id, show_id, seat_id, req_id, pay=None, txn="", *, kind="BOOK_SEAT", payload=None):
        meta = payload or {"show_id": show_id, "seat_id": seat_id}
        with self.lock:
            already = self._lookup(req_id, user_id=user_id, kind=kind, payload=meta)
            if already is not None:
                return already
        picked = parse_seats(seat_id)
        if len(picked) > 5:
            return self._finish(
                req_id, Outcome("INVALID_REQUEST", "You can book at most 5 seats in one session."), user_id, kind, meta
            )
        if not picked:
            return self._finish(req_id, Outcome("NOT_FOUND", "Show or seat does not exist."), user_id, kind, meta)
        show_seats = self.grid.get(show_id, {})
        with self._seat_locks(show_id, picked), self.lock:
            already = self._lookup(req_id, user_id=user_id, kind=kind, payload=meta)
            if already is not None:
                return already
            targets = [show_seats.get(seat) for seat in picked]
            if any(one is None for one in targets):
                return self._finish(req_id, Outcome("NOT_FOUND", "Show or seat does not exist."), user_id, kind, meta)
            if any(one.status == "BOOKED" for one in targets):
                return self._finish(
                    req_id, Outcome("ALREADY_BOOKED", "This seat is already booked."), user_id, kind, meta
                )
            if pay:
                pay_status, pay_msg, txn = pay()
                if pay_status != "SUCCESS":
                    return self._finish(req_id, Outcome(pay_status, pay_msg), user_id, kind, meta)
            bid = str(uuid5(NAMESPACE_URL, f"ticket-booking/{req_id}"))
            for one in targets:
                one.status = "BOOKED"
                one.owner = user_id
                one.booking = bid
            self.bookings[bid] = Booking(bid, user_id, show_id, ",".join(picked), txn=txn)
            return self._finish(req_id, Outcome("OK", "Booking confirmed.", bid, txn), user_id, kind, meta)

    def cancel(self, user_id, booking_id, req_id, *, kind="CANCEL_SEAT", payload=None):
        meta = payload or {"booking_id": booking_id}
        with self.lock:
            already = self._lookup(req_id, user_id=user_id, kind=kind, payload=meta)
            if already is not None:
                return already
            rec = self.bookings.get(booking_id)
        if rec is None:
            return self._finish(req_id, Outcome("NOT_FOUND", "Booking does not exist."), user_id, kind, meta)
        if rec.user_id != user_id:
            return self._finish(req_id, Outcome("FORBIDDEN", "Booking belongs to another user."), user_id, kind, meta)
        picked = parse_seats(rec.seat_id)
        with self._seat_locks(rec.show_id, picked), self.lock:
            already = self._lookup(req_id, user_id=user_id, kind=kind, payload=meta)
            if already is not None:
                return already
            rec = self.bookings.get(booking_id)
            if rec is None:
                return self._finish(req_id, Outcome("NOT_FOUND", "Booking does not exist."), user_id, kind, meta)
            if rec.status == "CANCELLED":
                out = Outcome("ALREADY_CANCELLED", "Booking is already cancelled.", booking_id, rec.txn)
                return self._finish(req_id, out, user_id, kind, meta)
            for seat in picked:
                one = self.grid[rec.show_id][seat]
                one.status = "AVAILABLE"
                one.owner = None
                one.booking = None
            rec.status = "CANCELLED"
            return self._finish(req_id, Outcome("OK", "Booking cancelled.", booking_id, rec.txn), user_id, kind, meta)

    def user_bookings(self, user_id):
        with self.lock:
            out = []
            for b in self.bookings.values():
                if b.user_id == user_id:
                    out.append(
                        {
                            "booking_id": b.booking_id,
                            "show_id": b.show_id,
                            "seat_id": b.seat_id,
                            "status": b.status,
                            "txn": b.txn,
                        }
                    )
            return out

    def digest_rows(self):
        with self.lock:
            rows = []
            for show in sorted(self.grid):
                for sid in sorted(self.grid[show]):
                    s = self.grid[show][sid]
                    rows.append(("seat", show, sid, s.status, s.owner or "", s.booking or ""))
            for bid in sorted(self.bookings):
                b = self.bookings[bid]
                rows.append(("booking", bid, b.user_id, b.show_id, b.seat_id, b.status, b.txn))
            return rows

    def result(self, req_id, user_id=None, kind=None, payload=None):
        self.lock.acquire()
        try:
            if req_id is None:
                return None
            if user_id is None and kind is None and payload is None:
                return self.seen.get(req_id)
            return self._lookup(req_id, user_id=user_id, kind=kind, payload=payload)
        finally:
            self.lock.release()
