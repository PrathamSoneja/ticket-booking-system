import re
from dataclasses import dataclass

from nltk.corpus import stopwords


@dataclass(frozen=True)
class KnowledgeSnippet:
    topic_id: str
    title: str
    keywords: tuple[str, ...]
    content: str


KB = [
    KnowledgeSnippet("cancellation_policy", "Cancellation Policy", ("cancel", "cancellation", "cancelling", "release", "drop"), "Bookings can be cancelled up to 2 hours prior to showtime via the CANCEL_SEAT request. Once cancelled, the seat is immediately released back to AVAILABLE status for other customers."),
    KnowledgeSnippet("refund_policy", "Refund Policy", ("refund", "refundable", "money", "payment", "reimbursement", "cost", "price"), "Full refunds are automatically processed to the original mock payment method for any cancellation confirmed at least 2 hours before showtime. Refund transactions typically complete instantly."),
    KnowledgeSnippet("seat_availability", "Seat Availability and Real-time Status", ("seat", "seats", "available", "status", "availability", "view", "browse", "empty"), "Seat availability is maintained with strong consistency across replicas. Users can view real-time availability by querying the SEATS endpoint with a valid show_id. Seats marked AVAILABLE can be reserved immediately."),
    KnowledgeSnippet("booking_window", "Advance Booking Window", ("advance", "window", "early", "when", "days", "time", "hours", "schedule"), "Shows open for ticket bookings 30 days in advance of the scheduled date. Online reservations close strictly 15 minutes before the show start time."),
    KnowledgeSnippet("seat_change_policy", "Modifying or Changing Seats", ("change", "modify", "switch", "transfer", "exchange", "different"), "Direct seat changes are not supported by the booking system. To switch seats, customers must first cancel their existing reservation (releasing the seat) and then book the new preferred seat."),
    KnowledgeSnippet("booking_procedure", "How to Book a Seat", ("how to book", "book a seat", "procedure", "steps to book"), "To book a seat: 1) Log in with valid credentials to obtain a session token. 2) Browse available shows and seats using the GET API. 3) Submit a BOOK_SEAT POST request with show_id and seat_id. The system confirms payment and locks the seat."),
]


class FAQRetriever:
    def __init__(self, snippets: list[KnowledgeSnippet] | None = None):
        self.snippets = list(snippets) if snippets is not None else list(KB)

    def set_knowledge_base(self, snippets: list[KnowledgeSnippet]) -> None:
        self.snippets = list(snippets)

    def _tokens(self, text: str) -> set[str]:
        return set(re.findall(r"\b[a-zA-Z0-9_-]+\b", text.lower())) - set(stopwords.words("english"))

    def retrieve(self, query: str, top_k: int = 2) -> tuple[str, list[str]]:
        tokens = self._tokens(query)
        if not tokens or not self.snippets:
            return "", []
        query_l = query.lower()
        ranked: list[tuple[float, KnowledgeSnippet]] = []
        for snippet in self.snippets:
            score = sum(10 for word in snippet.keywords if word.lower() in query_l)
            score += 3 * len(tokens & self._tokens(snippet.title))
            score += len(tokens & self._tokens(snippet.content))
            if score:
                ranked.append((score, snippet))
        ranked.sort(key=lambda item: item[0], reverse=True)
        picked = [snippet for _, snippet in ranked[:top_k]]
        return "\n\n".join(f"[{snippet.title}]\n{snippet.content}" for snippet in picked), [snippet.topic_id for snippet in picked]
