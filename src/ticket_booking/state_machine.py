from dataclasses import dataclass
from threading import Lock
from uuid import NAMESPACE_URL, uuid5

from .faq_data import loaded_shows


@dataclass(frozen=True)
class Outcome:
    status: str
    message: str
    booking_id: str = ""
    payment_transaction_id: str = ""


@dataclass
class SeatInfo:
    seat_id: str
    seat_status: str = "AVAILABLE"
    booked_by_user: str = None
    linked_booking_id: str = None


@dataclass
class BookingInfo:
    booking_id: str
    user_id: str
    show_id: str
    seat_id: str
    booking_status: str = "CONFIRMED"
    payment_transaction_id: str = ""


class BookingStateMachine:
    def __init__(self, show_data=None, num_seats_per_show=50):
        if num_seats_per_show < 1:
            raise ValueError("seats_per_show must be positive")

        if show_data:
            self.show_data = show_data
        else:
            self.show_data = loaded_shows

        self.seat_data = {}
        self.seat_locks = {}
        for show_key in self.show_data:
            seats_for_this_show = {}
            i = 1
            while i <= num_seats_per_show:
                seat_key = f"A{i}"
                seats_for_this_show[seat_key] = SeatInfo(seat_key)
                self.seat_locks[show_key + "/" + seat_key] = Lock()
                i = i + 1
            self.seat_data[show_key] = seats_for_this_show

        self.booking_records = {}
        self.past_outcomes = {}
        self.data_lock = Lock()
        self.lock_box = Lock()

    def shows(self):
        result_list = []
        for show_key, show_val in self.show_data.items():
            merged = {"show_id": show_key}
            merged.update(show_val)
            result_list.append(merged)
        return result_list

    def seats(self, show_id):
        seats_here = self.seat_data.get(show_id)
        if seats_here is None:
            return None
        out_list = []
        for s in seats_here.values():
            out_list.append({"seat_id": s.seat_id, "status": s.seat_status})
        return out_list

    def book(self, user_id, show_id, seat_id, req_id, charge_fn=None):
        self.data_lock.acquire()
        already = self.past_outcomes.get(req_id)
        self.data_lock.release()
        if already:
            return already

        picked = []
        for part in str(seat_id).replace(";", ",").split(","):
            bit = part.strip()
            if bit != "" and bit not in picked:
                picked.append(bit)

        if len(picked) > 5:
            out = Outcome("INVALID_REQUEST", "You can book at most 5 seats in one session.")
            self.data_lock.acquire()
            self.past_outcomes[req_id] = out
            self.data_lock.release()
            return out

        if not picked:
            out = Outcome("NOT_FOUND", "Show or seat does not exist.")
            self.data_lock.acquire()
            self.past_outcomes[req_id] = out
            self.data_lock.release()
            return out

        show_seats = self.seat_data.get(show_id, {})
        lock_keys = []
        for s in picked:
            lock_keys.append(show_id + "/" + s)
        lock_keys.sort()

        held = []
        self.lock_box.acquire()
        wanted = []
        for k in lock_keys:
            if k not in self.seat_locks:
                self.seat_locks[k] = Lock()
            wanted.append(self.seat_locks[k])
        self.lock_box.release()
        for L in wanted:
            L.acquire()
            held.append(L)
        try:
            self.data_lock.acquire()
            already = self.past_outcomes.get(req_id)
            self.data_lock.release()
            if already:
                return already

            targets = []
            for s in picked:
                one = show_seats.get(s)
                if one is None:
                    out = Outcome("NOT_FOUND", "Show or seat does not exist.")
                    self.data_lock.acquire()
                    self.past_outcomes[req_id] = out
                    self.data_lock.release()
                    return out
                targets.append(one)

            for one in targets:
                if one.seat_status == "BOOKED":
                    out = Outcome("ALREADY_BOOKED", "This seat is already booked.")
                    self.data_lock.acquire()
                    self.past_outcomes[req_id] = out
                    self.data_lock.release()
                    return out

            txn_id_val = ""
            if charge_fn:
                pay_status, pay_msg, txn_id_val = charge_fn()
                if pay_status != "SUCCESS":
                    out = Outcome(pay_status, pay_msg)
                    self.data_lock.acquire()
                    self.past_outcomes[req_id] = out
                    self.data_lock.release()
                    return out

            new_booking_id = str(uuid5(NAMESPACE_URL, f"ticket-booking/{req_id}"))
            joined = ",".join(picked)
            for one in targets:
                one.seat_status = "BOOKED"
                one.booked_by_user = user_id
                one.linked_booking_id = new_booking_id

            self.data_lock.acquire()
            self.booking_records[new_booking_id] = BookingInfo(
                new_booking_id, user_id, show_id, joined, payment_transaction_id=txn_id_val
            )
            out = Outcome("OK", "Booking confirmed.", new_booking_id, txn_id_val)
            self.past_outcomes[req_id] = out
            self.data_lock.release()
            return out
        finally:
            i = len(held) - 1
            while i >= 0:
                held[i].release()
                i = i - 1

    def cancel(self, user_id, booking_id, req_id):
        self.data_lock.acquire()
        already = self.past_outcomes.get(req_id)
        self.data_lock.release()
        if already:
            return already

        self.data_lock.acquire()
        the_booking = self.booking_records.get(booking_id)
        self.data_lock.release()
        if the_booking is None:
            out = Outcome("NOT_FOUND", "Booking does not exist.")
            self.data_lock.acquire()
            self.past_outcomes[req_id] = out
            self.data_lock.release()
            return out

        if the_booking.user_id != user_id:
            out = Outcome("FORBIDDEN", "Booking belongs to another user.")
            self.data_lock.acquire()
            self.past_outcomes[req_id] = out
            self.data_lock.release()
            return out

        picked = []
        for part in the_booking.seat_id.split(","):
            bit = part.strip()
            if bit != "":
                picked.append(bit)

        lock_keys = []
        for s in picked:
            lock_keys.append(the_booking.show_id + "/" + s)
        lock_keys.sort()
        held = []
        self.lock_box.acquire()
        wanted = []
        for k in lock_keys:
            if k not in self.seat_locks:
                self.seat_locks[k] = Lock()
            wanted.append(self.seat_locks[k])
        self.lock_box.release()
        for L in wanted:
            L.acquire()
            held.append(L)
        try:
            self.data_lock.acquire()
            already = self.past_outcomes.get(req_id)
            if already:
                self.data_lock.release()
                return already
            the_booking = self.booking_records.get(booking_id)
            self.data_lock.release()

            if the_booking.booking_status == "CANCELLED":
                out = Outcome(
                    "ALREADY_CANCELLED",
                    "Booking is already cancelled.",
                    booking_id,
                    the_booking.payment_transaction_id,
                )
                self.data_lock.acquire()
                self.past_outcomes[req_id] = out
                self.data_lock.release()
                return out

            for s in picked:
                related_seat = self.seat_data[the_booking.show_id][s]
                related_seat.seat_status = "AVAILABLE"
                related_seat.booked_by_user = None
                related_seat.linked_booking_id = None
            the_booking.booking_status = "CANCELLED"

            out = Outcome("OK", "Booking cancelled.", booking_id, the_booking.payment_transaction_id)
            self.data_lock.acquire()
            self.past_outcomes[req_id] = out
            self.data_lock.release()
            return out
        finally:
            i = len(held) - 1
            while i >= 0:
                held[i].release()
                i = i - 1
