

from dataclasses import dataclass
from threading import Lock
from uuid import uuid4


@dataclass(frozen=True)
class PaymentResult:
    status: str
    transaction_id: str = ""
    amount: float = 0.0
    message: str = ""


class PaymentGateway:
    def __init__(self, default_ticket_price: float = 50.0):
        self.default_ticket_price = default_ticket_price
        self._lock = Lock()
        self._processed_transactions: dict[str, PaymentResult] = {}
        self._refunds_by_tx: dict[str, PaymentResult] = {}
        self._success_by_tx_id: dict[str, PaymentResult] = {}

    def successful_charge_count(self) -> int:
        with self._lock:
            return sum(result.status == "SUCCESS" for result in self._processed_transactions.values())

    def process_payment(self, user_id: str, amount: float | None = None, card_number: str = "4242424242424242", idempotency_key: str | None = None) -> PaymentResult:
        del user_id
        with self._lock:
            if idempotency_key and idempotency_key in self._processed_transactions:
                return self._processed_transactions[idempotency_key]
            cost = self.default_ticket_price if amount is None else amount
            card = card_number.replace(" ", "").replace("-", "")
            if cost <= 0:
                result = PaymentResult("INVALID_AMOUNT", amount=cost, message="Payment amount must be greater than zero.")
            elif card.startswith("0000") or card.endswith("0000"):
                result = PaymentResult("PAYMENT_FAILED", amount=cost, message="Card declined by mock payment issuer.")
            else:
                tx = f"tx_{uuid4().hex[:12]}"
                result = PaymentResult("SUCCESS", tx, cost, "Payment processed successfully.")
                self._success_by_tx_id[tx] = result
            if idempotency_key:
                self._processed_transactions[idempotency_key] = result
            return result

    def refund_payment(self, transaction_id: str) -> PaymentResult:
        with self._lock:
            if not transaction_id:
                return PaymentResult("NOT_FOUND", message="No payment transaction to refund.")
            old = self._refunds_by_tx.get(transaction_id)
            if old:
                return old
            payment = self._success_by_tx_id.get(transaction_id)
            if payment is None:
                return PaymentResult("NOT_FOUND", message=f"Transaction {transaction_id} not found.")
            result = PaymentResult("SUCCESS", f"ref_{uuid4().hex[:12]}", payment.amount, f"Refund of ${payment.amount:.2f} processed for transaction {transaction_id}.")
            self._refunds_by_tx[transaction_id] = result
            return result
