from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from uuid import NAMESPACE_URL, uuid5


@dataclass(frozen=True)
class Outcome:
    status: str
    message: str
    booking_id: str = ""
    payment_transaction_id: str = ""


@dataclass
class Seat:
    seat_id: str
    status: str = "AVAILABLE"
    booked_by: str | None = None
    booking_id: str | None = None


@dataclass
class Booking:
    booking_id: str
    user_id: str
    show_id: str
    seat_id: str
    status: str = "CONFIRMED"
    payment_transaction_id: str = ""


SHOWS = {
    "show-1": {"name": "The Raft Musical", "venue": "Auditorium A", "start_time": "2026-10-01T19:00:00Z"},
    "show-2": {"name": "Distributed Dreams", "venue": "Auditorium B", "start_time": "2026-10-02T19:00:00Z"},
}


class BookingStateMachine:
    def __init__(self, shows: dict[str, dict[str, str]] | None = None, seats_per_show: int = 10):
        if seats_per_show < 1:
            raise ValueError("seats_per_show must be positive")
        self._shows = shows or SHOWS
        self._seats = {
            show: {f"A{i}": Seat(f"A{i}") for i in range(1, seats_per_show + 1)}
            for show in self._shows
        }
        self._bookings: dict[str, Booking] = {}
        self._outcomes: dict[str, Outcome] = {}
        self._lock = Lock()

    def shows(self) -> list[dict[str, str]]:
        return [{"show_id": key, **value} for key, value in self._shows.items()]

    def seats(self, show_id: str) -> list[dict[str, str]] | None:
        seats = self._seats.get(show_id)
        if seats is None:
            return None
        return [{"seat_id": seat.seat_id, "status": seat.status} for seat in seats.values()]

    def book(
        self,
        user_id: str,
        show_id: str,
        seat_id: str,
        request_id: str,
        charge: Callable[[], tuple[str, str, str]] | None = None,
    ) -> Outcome:
        with self._lock:
            old = self._outcomes.get(request_id)
            if old:
                return old
            seat = self._seats.get(show_id, {}).get(seat_id)
            if seat is None:
                return self._save(request_id, Outcome("NOT_FOUND", "Show or seat does not exist."))
            if seat.status == "BOOKED":
                return self._save(request_id, Outcome("ALREADY_BOOKED", "This seat is already booked."))
            tx = ""
            if charge:
                status, msg, tx = charge()
                if status != "SUCCESS":
                    return self._save(request_id, Outcome(status, msg))
            booking_id = str(uuid5(NAMESPACE_URL, f"ticket-booking/{request_id}"))
            seat.status, seat.booked_by, seat.booking_id = "BOOKED", user_id, booking_id
            self._bookings[booking_id] = Booking(booking_id, user_id, show_id, seat_id, payment_transaction_id=tx)
            return self._save(request_id, Outcome("OK", "Booking confirmed.", booking_id, tx))

    def cancel(self, user_id: str, booking_id: str, request_id: str) -> Outcome:
        with self._lock:
            old = self._outcomes.get(request_id)
            if old:
                return old
            booking = self._bookings.get(booking_id)
            if booking is None:
                return self._save(request_id, Outcome("NOT_FOUND", "Booking does not exist."))
            if booking.user_id != user_id:
                return self._save(request_id, Outcome("FORBIDDEN", "Booking belongs to another user."))
            if booking.status == "CANCELLED":
                return self._save(request_id, Outcome("ALREADY_CANCELLED", "Booking is already cancelled.", booking_id, booking.payment_transaction_id))
            seat = self._seats[booking.show_id][booking.seat_id]
            seat.status, seat.booked_by, seat.booking_id = "AVAILABLE", None, None
            booking.status = "CANCELLED"
            return self._save(request_id, Outcome("OK", "Booking cancelled.", booking_id, booking.payment_transaction_id))

    def _save(self, request_id: str, out: Outcome) -> Outcome:
        self._outcomes[request_id] = out
        return out
