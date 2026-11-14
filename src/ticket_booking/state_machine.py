import json
from dataclasses import dataclass
from threading import Lock
from uuid import NAMESPACE_URL, uuid5

from .seed import loaded_shows


@dataclass(frozen=True)
class Outcome:
    status: str
    message: str
    booking_id: str = ""
    txn: str = ""


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
            i = 1
            while i <= capacity:
                key = f"A{i}"
                row[key] = Seat(key)
                self.locks[show + "/" + key] = Lock()
                i = i + 1
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

    def book(self, user_id, show_id, seat_id, req_id, pay=None, txn="", *, kind="BOOK_SEAT", payload=None):
        with self.lock:
            already = self._lookup(req_id, user_id=user_id, kind=kind, payload=payload or {"show_id": show_id, "seat_id": seat_id})
            if already is not None:
                return already
        picked = []
        for part in str(seat_id).replace(";", ",").split(","):
            bit = part.strip()
            if bit != "" and bit not in picked:
                picked.append(bit)
        if len(picked) > 5:
            out = Outcome("INVALID_REQUEST", "You can book at most 5 seats in one session.")
            self._record(req_id, out, user_id=user_id, kind=kind, payload={"show_id": show_id, "seat_id": seat_id})
            return out
        if not picked:
            out = Outcome("NOT_FOUND", "Show or seat does not exist.")
            self._record(req_id, out, user_id=user_id, kind=kind, payload={"show_id": show_id, "seat_id": seat_id})
            return out
        show_seats = self.grid.get(show_id, {})
        keys = []
        for s in picked:
            keys.append(show_id + "/" + s)
        keys.sort()
        held = []
        self.box.acquire()
        wanted = []
        for k in keys:
            if k not in self.locks:
                self.locks[k] = Lock()
            wanted.append(self.locks[k])
        self.box.release()
        for L in wanted:
            L.acquire()
            held.append(L)
        try:
            with self.lock:
                already = self._lookup(req_id, user_id=user_id, kind=kind, payload=payload or {"show_id": show_id, "seat_id": seat_id})
                if already is not None:
                    return already
                targets = []
                for s in picked:
                    one = show_seats.get(s)
                    if one is None:
                        out = Outcome("NOT_FOUND", "Show or seat does not exist.")
                        self._record(req_id, out, user_id=user_id, kind=kind, payload={"show_id": show_id, "seat_id": seat_id})
                        return out
                    targets.append(one)
                for one in targets:
                    if one.status == "BOOKED":
                        out = Outcome("ALREADY_BOOKED", "This seat is already booked.")
                        self._record(req_id, out, user_id=user_id, kind=kind, payload={"show_id": show_id, "seat_id": seat_id})
                        return out
                if pay:
                    pay_status, pay_msg, txn = pay()
                    if pay_status != "SUCCESS":
                        out = Outcome(pay_status, pay_msg)
                        self._record(req_id, out, user_id=user_id, kind=kind, payload={"show_id": show_id, "seat_id": seat_id})
                        return out
                bid = str(uuid5(NAMESPACE_URL, f"ticket-booking/{req_id}"))
                joined = ",".join(picked)
                for one in targets:
                    one.status = "BOOKED"
                    one.owner = user_id
                    one.booking = bid
                self.bookings[bid] = Booking(bid, user_id, show_id, joined, txn=txn)
                out = Outcome("OK", "Booking confirmed.", bid, txn)
                self._record(req_id, out, user_id=user_id, kind=kind, payload={"show_id": show_id, "seat_id": seat_id})
                return out
        finally:
            i = len(held) - 1
            while i >= 0:
                held[i].release()
                i = i - 1

    def cancel(self, user_id, booking_id, req_id, *, kind="CANCEL_SEAT", payload=None):
        with self.lock:
            already = self._lookup(req_id, user_id=user_id, kind=kind, payload=payload or {"booking_id": booking_id})
            if already is not None:
                return already
            rec = self.bookings.get(booking_id)
        if rec is None:
            out = Outcome("NOT_FOUND", "Booking does not exist.")
            self._record(req_id, out, user_id=user_id, kind=kind, payload={"booking_id": booking_id})
            return out
        if rec.user_id != user_id:
            out = Outcome("FORBIDDEN", "Booking belongs to another user.")
            self._record(req_id, out, user_id=user_id, kind=kind, payload={"booking_id": booking_id})
            return out
        picked = []
        for part in rec.seat_id.split(","):
            bit = part.strip()
            if bit != "":
                picked.append(bit)
        keys = []
        for s in picked:
            keys.append(rec.show_id + "/" + s)
        keys.sort()
        held = []
        self.box.acquire()
        wanted = []
        for k in keys:
            if k not in self.locks:
                self.locks[k] = Lock()
            wanted.append(self.locks[k])
        self.box.release()
        for L in wanted:
            L.acquire()
            held.append(L)
        try:
            with self.lock:
                already = self._lookup(req_id, user_id=user_id, kind=kind, payload=payload or {"booking_id": booking_id})
                if already is not None:
                    return already
                rec = self.bookings.get(booking_id)
                if rec is None:
                    out = Outcome("NOT_FOUND", "Booking does not exist.")
                    self._record(req_id, out, user_id=user_id, kind=kind, payload={"booking_id": booking_id})
                    return out
                if rec.status == "CANCELLED":
                    out = Outcome("ALREADY_CANCELLED", "Booking is already cancelled.", booking_id, rec.txn)
                    self._record(req_id, out, user_id=user_id, kind=kind, payload={"booking_id": booking_id})
                    return out
                for s in picked:
                    one = self.grid[rec.show_id][s]
                    one.status = "AVAILABLE"
                    one.owner = None
                    one.booking = None
                rec.status = "CANCELLED"
                out = Outcome("OK", "Booking cancelled.", booking_id, rec.txn)
                self._record(req_id, out, user_id=user_id, kind=kind, payload={"booking_id": booking_id})
                return out
        finally:
            i = len(held) - 1
            while i >= 0:
                held[i].release()
                i = i - 1

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
