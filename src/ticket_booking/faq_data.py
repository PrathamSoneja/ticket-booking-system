import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from nltk.corpus import stopwords


@dataclass(frozen=True)
class FaqTopic:
    topic_key: str
    display_title: str
    trigger_words: tuple
    body_text: str


DEFAULT_TOPICS = [
    FaqTopic(
        "cancellation_policy",
        "Cancellation Policy",
        ("cancel", "cancellation", "cancelling", "release", "drop"),
        "Bookings can be cancelled up to 2 hours prior to showtime via the CANCEL_SEAT request. Once cancelled, the seat is immediately released back to AVAILABLE status for other customers.",
    ),
    FaqTopic(
        "refund_policy",
        "Refund Policy",
        ("refund", "refundable", "money", "payment", "reimbursement", "cost", "price"),
        "Full refunds are automatically processed to the original mock payment method for any cancellation confirmed at least 2 hours before showtime. Refund transactions typically complete instantly.",
    ),
    FaqTopic(
        "seat_availability",
        "Seat Availability and Real-time Status",
        ("seat", "seats", "available", "status", "availability", "view", "browse", "empty"),
        "Seat availability is maintained with strong consistency across replicas. Users can view real-time availability by querying the SEATS endpoint with a valid show_id. Seats marked AVAILABLE can be reserved immediately.",
    ),
    FaqTopic(
        "booking_window",
        "Advance Booking Window",
        ("advance", "window", "early", "when", "days", "time", "hours", "schedule"),
        "Shows open for ticket bookings 30 days in advance of the scheduled date. Online reservations close strictly 15 minutes before the show start time.",
    ),
    FaqTopic(
        "seat_change_policy",
        "Modifying or Changing Seats",
        ("change", "modify", "switch", "transfer", "exchange", "different"),
        "Direct seat changes are not supported by the booking system. To switch seats, customers must first cancel their existing reservation (releasing the seat) and then book the new preferred seat.",
    ),
    FaqTopic(
        "booking_procedure",
        "How to Book a Seat",
        ("how to book", "book a seat", "procedure", "steps to book"),
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
    catalog.executemany(
        "INSERT INTO shows VALUES (?,?,?,?)",
        [
            ("show-1", "The Raft Musical", "Auditorium A", "2026-10-01T19:00:00Z"),
            ("show-2", "Distributed Dreams", "Auditorium B", "2026-10-02T19:00:00Z"),
            ("show-3", "Consensus Comedy", "Auditorium C", "2026-10-03T18:30:00Z"),
            ("show-4", "Replica Nights", "Hall D", "2026-10-04T20:00:00Z"),
            ("show-5", "Leader Election Live", "Hall E", "2026-10-05T19:30:00Z"),
            ("show-6", "Log Replay", "Studio F", "2026-10-06T17:00:00Z"),
            ("show-7", "Quorum Jazz", "Studio G", "2026-10-07T21:00:00Z"),
            ("show-8", "Heartbeat", "Open Air", "2026-10-08T19:00:00Z"),
        ],
    )
if catalog.execute("SELECT COUNT(*) FROM faq").fetchone()[0] == 0:
    for t in DEFAULT_TOPICS:
        catalog.execute(
            "INSERT INTO faq VALUES (?,?,?,?)",
            (t.topic_key, t.display_title, ",".join(t.trigger_words), t.body_text),
        )
catalog.commit()

loaded_shows = {}
for row in catalog.execute("SELECT show_id, name, venue, start_time FROM shows"):
    loaded_shows[row[0]] = {"name": row[1], "venue": row[2], "start_time": row[3]}


class FaqSearchEngine:
    def __init__(self, topic_list=None):
        if topic_list is not None:
            self.topic_list = list(topic_list)
        else:
            self.topic_list = []
            for row in catalog.execute("SELECT topic_key, display_title, trigger_words, body_text FROM faq"):
                self.topic_list.append(FaqTopic(row[0], row[1], tuple(row[2].split(",")), row[3]))

    def set_knowledge_base(self, new_topics):
        self.topic_list = list(new_topics)

    def retrieve(self, user_query, top_k=2):
        word_set = set(re.findall(r"\b[a-zA-Z0-9_-]+\b", user_query.lower()))
        stop_word_set = set(stopwords.words("english"))
        query_words = word_set - stop_word_set

        if not query_words or not self.topic_list:
            return "", []

        query_as_lower = user_query.lower()
        scored_topics = []

        for topic in self.topic_list:
            this_score = 0
            for kw in topic.trigger_words:
                if kw.lower() in query_as_lower:
                    this_score = this_score + 10

            title_words = set(re.findall(r"\b[a-zA-Z0-9_-]+\b", topic.display_title.lower())) - stop_word_set
            this_score = this_score + 3 * len(query_words & title_words)

            body_words = set(re.findall(r"\b[a-zA-Z0-9_-]+\b", topic.body_text.lower())) - stop_word_set
            this_score = this_score + len(query_words & body_words)

            if this_score:
                scored_topics.append((this_score, topic))

        scored_topics.sort(key=lambda pair: pair[0], reverse=True)

        top_topics = []
        i = 0
        for score_val, topic_val in scored_topics:
            if i >= top_k:
                break
            top_topics.append(topic_val)
            i = i + 1

        text_chunks = []
        id_list = []
        for t in top_topics:
            text_chunks.append(f"[{t.display_title}]\n{t.body_text}")
            id_list.append(t.topic_key)

        return "\n\n".join(text_chunks), id_list


FAQRetriever = FaqSearchEngine
