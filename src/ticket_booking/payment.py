from dataclasses import dataclass
from threading import Lock
from uuid import uuid4


@dataclass(frozen=True)
class PaymentResultInfo:
    status_txt: str
    txn_id: str = ""
    charged_amount: float = 0.0
    info_message: str = ""


class PaymentGateway:
    def __init__(self, ticket_price_default=50.0):
        self.ticket_price_default = ticket_price_default
        self.my_lock = Lock()
        self.done_payments_by_key = {}
        self.done_refunds_by_txn = {}
        self.txn_lookup = {}

    def successful_charge_count(self):
        self.my_lock.acquire()
        cnt = 0
        for v in self.done_payments_by_key.values():
            if v.status_txt == "SUCCESS":
                cnt = cnt + 1
        self.my_lock.release()
        return cnt

    def process_payment(self, whos_paying, pay_amount=None, card_num="4242424242424242", idempotency_key=None):
        self.my_lock.acquire()
        try:
            if idempotency_key and idempotency_key in self.done_payments_by_key:
                return self.done_payments_by_key[idempotency_key]

            if pay_amount is None:
                final_cost = self.ticket_price_default
            else:
                final_cost = pay_amount

            clean_card = card_num.replace(" ", "").replace("-", "")

            if final_cost <= 0:
                new_result = PaymentResultInfo("INVALID_AMOUNT", charged_amount=final_cost, info_message="Payment amount must be greater than zero.")
            elif clean_card.startswith("0000") or clean_card.endswith("0000"):
                new_result = PaymentResultInfo("PAYMENT_FAILED", charged_amount=final_cost, info_message="Card declined by mock payment issuer.")
            else:
                new_txn_id = f"tx_{uuid4().hex[:12]}"
                new_result = PaymentResultInfo("SUCCESS", new_txn_id, final_cost, "Payment processed successfully.")
                self.txn_lookup[new_txn_id] = new_result

            if idempotency_key:
                self.done_payments_by_key[idempotency_key] = new_result

            return new_result
        finally:
            self.my_lock.release()

    def refund_payment(self, txn_id):
        self.my_lock.acquire()
        try:
            if not txn_id:
                return PaymentResultInfo("NOT_FOUND", info_message="No payment transaction to refund.")

            already_done = self.done_refunds_by_txn.get(txn_id)
            if already_done:
                return already_done

            original_payment = self.txn_lookup.get(txn_id)
            if original_payment is None:
                return PaymentResultInfo("NOT_FOUND", info_message=f"Transaction {txn_id} not found.")

            refund_res = PaymentResultInfo(
                "SUCCESS",
                f"ref_{uuid4().hex[:12]}",
                original_payment.charged_amount,
                f"Refund of ${original_payment.charged_amount:.2f} processed for transaction {txn_id}.",
            )
            self.done_refunds_by_txn[txn_id] = refund_res
            return refund_res
        finally:
            self.my_lock.release()
