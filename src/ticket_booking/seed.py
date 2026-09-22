import sqlite3
from pathlib import Path

show_rows = [
    ("show-1", "The Raft Musical", "Auditorium A", "2026-10-01T19:00:00Z"),
    ("show-2", "Distributed Dreams", "Auditorium B", "2026-10-02T19:00:00Z"),
    ("show-3", "Consensus Comedy", "Auditorium C", "2026-10-03T18:30:00Z"),
    ("show-4", "Replica Nights", "Hall D", "2026-10-04T20:00:00Z"),
    ("show-5", "Leader Election Live", "Hall E", "2026-10-05T19:30:00Z"),
    ("show-6", "Log Replay", "Studio F", "2026-10-06T17:00:00Z"),
    ("show-7", "Quorum Jazz", "Studio G", "2026-10-07T21:00:00Z"),
    ("show-8", "Heartbeat", "Open Air", "2026-10-08T19:00:00Z"),
]

faq_rows = [
    (
        "cancellation_policy",
        "Cancellation Policy",
        "cancel,cancellation,cancelling,release,drop",
        "Bookings can be cancelled up to 2 hours prior to showtime via the CANCEL_SEAT request. Once cancelled, the seat is immediately released back to AVAILABLE status for other customers.",
    ),
    (
        "refund_policy",
        "Refund Policy",
        "refund,refundable,money,payment,reimbursement,cost,price",
        "Full refunds are automatically processed to the original mock payment method for any cancellation confirmed at least 2 hours before showtime. Refund transactions typically complete instantly.",
    ),
    (
        "seat_availability",
        "Seat Availability and Real-time Status",
        "seat,seats,available,status,availability,view,browse,empty",
        "Seat availability is maintained with strong consistency across replicas. Users can view real-time availability by querying the SEATS endpoint with a valid show_id. Seats marked AVAILABLE can be reserved immediately.",
    ),
    (
        "booking_window",
        "Advance Booking Window",
        "advance,window,early,when,days,time,hours,schedule",
        "Shows open for ticket bookings 30 days in advance of the scheduled date. Online reservations close strictly 15 minutes before the show start time.",
    ),
    (
        "seat_change_policy",
        "Modifying or Changing Seats",
        "change,modify,switch,transfer,exchange,different",
        "Direct seat changes are not supported by the booking system. To switch seats, customers must first cancel their existing reservation (releasing the seat) and then book the new preferred seat.",
    ),
    (
        "booking_procedure",
        "How to Book a Seat",
        "how to book,book a seat,procedure,steps to book",
        "To book a seat: 1) Log in with valid credentials to obtain a session token. 2) Browse available shows and seats using the GET API. 3) Submit a BOOK_SEAT POST request with show_id and seat_id. The system confirms payment and locks the seat.",
    ),
]

DB_PATH = Path(__file__).resolve().parent / "catalog.db"
catalog = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
catalog.execute("PRAGMA journal_mode=WAL")
catalog.execute("CREATE TABLE IF NOT EXISTS shows (show_id TEXT PRIMARY KEY, name TEXT, venue TEXT, start_time TEXT)")
catalog.execute("CREATE TABLE IF NOT EXISTS faq (topic_key TEXT PRIMARY KEY, display_title TEXT, trigger_words TEXT, body_text TEXT)")
catalog.execute("CREATE INDEX IF NOT EXISTS idx_shows_name ON shows(name)")
catalog.execute("CREATE INDEX IF NOT EXISTS idx_faq_title ON faq(display_title)")
if catalog.execute("SELECT COUNT(*) FROM shows").fetchone()[0] == 0:
    catalog.executemany("INSERT INTO shows VALUES (?,?,?,?)", show_rows)
if catalog.execute("SELECT COUNT(*) FROM faq").fetchone()[0] == 0:
    catalog.executemany("INSERT INTO faq VALUES (?,?,?,?)", faq_rows)
catalog.commit()

loaded_shows = {}
for row in catalog.execute("SELECT show_id, name, venue, start_time FROM shows"):
    loaded_shows[row[0]] = {"name": row[1], "venue": row[2], "start_time": row[3]}
