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
        self.lock = Lock()
        self.box = Lock()

    def shows(self):
        out = []
        for sid, info in self.catalog.items():
            row = {"show_id": sid}
            row.update(info)
            out.append(row)
        return out

    def seats(self, show_id):
        here = self.grid.get(show_id)
        if here is None:
            return None
        out = []
        for s in here.values():
            out.append({"seat_id": s.seat_id, "status": s.status})
        return out

    def book(self, user_id, show_id, seat_id, req_id, pay=None):
        self.lock.acquire()
        already = self.seen.get(req_id)
        self.lock.release()
        if already:
            return already
        picked = []
        for part in str(seat_id).replace(";", ",").split(","):
            bit = part.strip()
            if bit != "" and bit not in picked:
                picked.append(bit)
        if len(picked) > 5:
            out = Outcome("INVALID_REQUEST", "You can book at most 5 seats in one session.")
            self.lock.acquire()
            self.seen[req_id] = out
            self.lock.release()
            return out
        if not picked:
            out = Outcome("NOT_FOUND", "Show or seat does not exist.")
            self.lock.acquire()
            self.seen[req_id] = out
            self.lock.release()
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
            self.lock.acquire()
            already = self.seen.get(req_id)
            self.lock.release()
            if already:
                return already
            targets = []
            for s in picked:
                one = show_seats.get(s)
                if one is None:
                    out = Outcome("NOT_FOUND", "Show or seat does not exist.")
                    self.lock.acquire()
                    self.seen[req_id] = out
                    self.lock.release()
                    return out
                targets.append(one)
            for one in targets:
                if one.status == "BOOKED":
                    out = Outcome("ALREADY_BOOKED", "This seat is already booked.")
                    self.lock.acquire()
                    self.seen[req_id] = out
                    self.lock.release()
                    return out
            txn = ""
            if pay:
                pay_status, pay_msg, txn = pay()
                if pay_status != "SUCCESS":
                    out = Outcome(pay_status, pay_msg)
                    self.lock.acquire()
                    self.seen[req_id] = out
                    self.lock.release()
                    return out
            bid = str(uuid5(NAMESPACE_URL, f"ticket-booking/{req_id}"))
            joined = ",".join(picked)
            for one in targets:
                one.status = "BOOKED"
                one.owner = user_id
                one.booking = bid
            self.lock.acquire()
            self.bookings[bid] = Booking(bid, user_id, show_id, joined, txn=txn)
            out = Outcome("OK", "Booking confirmed.", bid, txn)
            self.seen[req_id] = out
            self.lock.release()
            return out
        finally:
            i = len(held) - 1
            while i >= 0:
                held[i].release()
                i = i - 1

    def cancel(self, user_id, booking_id, req_id):
        self.lock.acquire()
        already = self.seen.get(req_id)
        self.lock.release()
        if already:
            return already
        self.lock.acquire()
        rec = self.bookings.get(booking_id)
        self.lock.release()
        if rec is None:
            out = Outcome("NOT_FOUND", "Booking does not exist.")
            self.lock.acquire()
            self.seen[req_id] = out
            self.lock.release()
            return out
        if rec.user_id != user_id:
            out = Outcome("FORBIDDEN", "Booking belongs to another user.")
            self.lock.acquire()
            self.seen[req_id] = out
            self.lock.release()
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
            self.lock.acquire()
            already = self.seen.get(req_id)
            if already:
                self.lock.release()
                return already
            rec = self.bookings.get(booking_id)
            self.lock.release()
            if rec.status == "CANCELLED":
                out = Outcome("ALREADY_CANCELLED", "Booking is already cancelled.", booking_id, rec.txn)
                self.lock.acquire()
                self.seen[req_id] = out
                self.lock.release()
                return out
            for s in picked:
                one = self.grid[rec.show_id][s]
                one.status = "AVAILABLE"
                one.owner = None
                one.booking = None
            rec.status = "CANCELLED"
            out = Outcome("OK", "Booking cancelled.", booking_id, rec.txn)
            self.lock.acquire()
            self.seen[req_id] = out
            self.lock.release()
            return out
        finally:
            i = len(held) - 1
            while i >= 0:
                held[i].release()
                i = i - 1
