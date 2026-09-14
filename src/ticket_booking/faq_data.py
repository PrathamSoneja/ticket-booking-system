"""Domain FAQ knowledge base and RAG-style context retrieval (FR-LLM-2, FR-LLM-3)."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class KnowledgeSnippet:
    topic_id: str
    title: str
    keywords: tuple[str, ...]
    content: str


DEFAULT_KNOWLEDGE_BASE: list[KnowledgeSnippet] = [
    KnowledgeSnippet(
        topic_id="cancellation_policy",
        title="Cancellation Policy",
        keywords=("cancel", "cancellation", "cancelling", "release", "drop"),
        content=(
            "Bookings can be cancelled up to 2 hours prior to showtime via the CANCEL_SEAT request. "
            "Once cancelled, the seat is immediately released back to AVAILABLE status for other customers."
        ),
    ),
    KnowledgeSnippet(
        topic_id="refund_policy",
        title="Refund Policy",
        keywords=("refund", "refundable", "money", "payment", "reimbursement", "cost", "price"),
        content=(
            "Full refunds are automatically processed to the original mock payment method for any "
            "cancellation confirmed at least 2 hours before showtime. Refund transactions typically complete instantly."
        ),
    ),
    KnowledgeSnippet(
        topic_id="seat_availability",
        title="Seat Availability and Real-time Status",
        keywords=("seat", "seats", "available", "status", "availability", "view", "browse", "empty"),
        content=(
            "Seat availability is maintained with strong consistency across replicas. "
            "Users can view real-time availability by querying the SEATS endpoint with a valid show_id. "
            "Seats marked AVAILABLE can be reserved immediately."
        ),
    ),
    KnowledgeSnippet(
        topic_id="booking_window",
        title="Advance Booking Window",
        keywords=("advance", "window", "early", "when", "days", "time", "hours", "schedule"),
        content=(
            "Shows open for ticket bookings 30 days in advance of the scheduled date. "
            "Online reservations close strictly 15 minutes before the show start time."
        ),
    ),
    KnowledgeSnippet(
        topic_id="seat_change_policy",
        title="Modifying or Changing Seats",
        keywords=("change", "modify", "switch", "transfer", "exchange", "different"),
        content=(
            "Direct seat changes are not supported by the booking system. To switch seats, "
            "customers must first cancel their existing reservation (releasing the seat) and then book the new preferred seat."
        ),
    ),
    KnowledgeSnippet(
        topic_id="booking_procedure",
        title="How to Book a Seat",
        keywords=("how to book", "book a seat", "procedure", "steps to book"),
        content=(
            "To book a seat: 1) Log in with valid credentials to obtain a session token. "
            "2) Browse available shows and seats using the GET API. "
            "3) Submit a BOOK_SEAT POST request with show_id and seat_id. The system confirms payment and locks the seat."
        ),
    ),
]

STOPWORDS = {"how", "do", "i", "can", "what", "is", "a", "the", "my", "to", "for", "of", "and", "in", "on", "are"}


class FAQRetriever:
    """Keyword and token-overlap based context retriever for RAG grounding."""

    def __init__(self, snippets: list[KnowledgeSnippet] | None = None):
        self.snippets = list(snippets) if snippets is not None else list(DEFAULT_KNOWLEDGE_BASE)

    def set_knowledge_base(self, snippets: list[KnowledgeSnippet]) -> None:
        self.snippets = list(snippets)

    def _tokenize(self, text: str, remove_stopwords: bool = True) -> set[str]:
        tokens = set(re.findall(r"\b[a-zA-Z0-9_-]+\b", text.lower()))
        if remove_stopwords:
            tokens = {t for t in tokens if t not in STOPWORDS}
        return tokens

    def retrieve(self, query: str, top_k: int = 2) -> tuple[str, list[str]]:
        """Find the most relevant knowledge snippets for a given query string.

        Returns (context_text, list_of_matched_topic_ids).
        """
        query_lower = query.lower()
        query_tokens = self._tokenize(query, remove_stopwords=True)
        if not query_tokens or not self.snippets:
            return "", []

        scored: list[tuple[float, KnowledgeSnippet]] = []
        for snippet in self.snippets:
            score = 0.0
            content_tokens = self._tokenize(snippet.content, remove_stopwords=True)
            title_tokens = self._tokenize(snippet.title, remove_stopwords=True)

            # Check full keyword phrase match in raw query
            for kw in snippet.keywords:
                if kw.lower() in query_lower:
                    score += 10.0

            # Content & title overlap with non-stopwords
            score += 3.0 * len(query_tokens & title_tokens)
            score += 1.0 * len(query_tokens & content_tokens)

            if score > 0:
                scored.append((score, snippet))

        scored.sort(key=lambda item: item[0], reverse=True)
        top = scored[:top_k]

        matched_ids = [snip.topic_id for _, snip in top]
        context = "\n\n".join(f"[{snip.title}]\n{snip.content}" for _, snip in top)
        return context, matched_ids
