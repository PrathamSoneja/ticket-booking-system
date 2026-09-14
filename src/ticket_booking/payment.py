"""Deterministic mock payment engine for ticket bookings (FR-BOOK-5)."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from uuid import uuid4


@dataclass(frozen=True)
class PaymentResult:
    status: str  # "SUCCESS", "PAYMENT_FAILED", "INVALID_AMOUNT", "NOT_FOUND"
    transaction_id: str = ""
    amount: float = 0.0
    message: str = ""


class PaymentGateway:
    """Mock payment service providing deterministic transaction outcomes for testing and demos."""

    def __init__(self, default_ticket_price: float = 50.0):
        self.default_ticket_price = default_ticket_price
        self._lock = Lock()
        self._processed_transactions: dict[str, PaymentResult] = {}
        self._refunds_by_tx: dict[str, PaymentResult] = {}
        self._success_by_tx_id: dict[str, PaymentResult] = {}

    def successful_charge_count(self) -> int:
        with self._lock:
            return sum(1 for result in self._processed_transactions.values() if result.status == "SUCCESS")

    def process_payment(
        self,
        user_id: str,
        amount: float | None = None,
        card_number: str = "4242424242424242",
        idempotency_key: str | None = None,
    ) -> PaymentResult:
        """Process a mock payment with deterministic failure rules:

        - card ending in '0000' or starting with '0000' triggers PAYMENT_FAILED.
        - amount <= 0 triggers INVALID_AMOUNT.
        - idempotency_key ensures idempotent replay of payment requests.
        """
        del user_id  # Mock issuer does not look up a customer account.
        with self._lock:
            if idempotency_key and idempotency_key in self._processed_transactions:
                return self._processed_transactions[idempotency_key]

            actual_amount = self.default_ticket_price if amount is None else amount
            if actual_amount <= 0:
                result = PaymentResult(
                    status="INVALID_AMOUNT",
                    amount=actual_amount,
                    message="Payment amount must be greater than zero.",
                )
            else:
                clean_card = card_number.replace(" ", "").replace("-", "")
                if clean_card.startswith("0000") or clean_card.endswith("0000"):
                    result = PaymentResult(
                        status="PAYMENT_FAILED",
                        amount=actual_amount,
                        message="Card declined by mock payment issuer.",
                    )
                else:
                    tx_id = f"tx_{uuid4().hex[:12]}"
                    result = PaymentResult(
                        status="SUCCESS",
                        transaction_id=tx_id,
                        amount=actual_amount,
                        message="Payment processed successfully.",
                    )
                    self._success_by_tx_id[tx_id] = result

            if idempotency_key:
                self._processed_transactions[idempotency_key] = result
            return result

    def refund_payment(self, transaction_id: str) -> PaymentResult:
        """Idempotent mock refund for a previously successful charge only."""
        with self._lock:
            if not transaction_id:
                return PaymentResult(status="NOT_FOUND", message="No payment transaction to refund.")
            previous = self._refunds_by_tx.get(transaction_id)
            if previous:
                return previous
            original = self._success_by_tx_id.get(transaction_id)
            if original is None:
                return PaymentResult(
                    status="NOT_FOUND",
                    message=f"Transaction {transaction_id} not found.",
                )
            refund = PaymentResult(
                status="SUCCESS",
                transaction_id=f"ref_{uuid4().hex[:12]}",
                amount=original.amount,
                message=f"Refund of ${original.amount:.2f} processed for transaction {transaction_id}.",
            )
            self._refunds_by_tx[transaction_id] = refund
            return refund
