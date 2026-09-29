from dataclasses import dataclass
from threading import Lock
from uuid import uuid4


@dataclass(frozen=True)
class PayResult:
    status: str
    txn: str = ""
    amount: float = 0.0
    message: str = ""


class Payments:
    def __init__(self, price=50.0):
        self.price = price
        self.lock = Lock()
        self.charges = {}
        self.refunds = {}
        self.txns = {}

    def charge_count(self):
        self.lock.acquire()
        cnt = 0
        for v in self.charges.values():
            if v.status == "SUCCESS":
                cnt = cnt + 1
        self.lock.release()
        return cnt

    def charge(self, user, amount=None, card="4242424242424242", req_id=None):
        self.lock.acquire()
        try:
            if req_id and req_id in self.charges:
                return self.charges[req_id]
            if amount is None:
                cost = self.price
            else:
                cost = amount
            clean = card.replace(" ", "").replace("-", "")
            if cost <= 0:
                out = PayResult("INVALID_AMOUNT", amount=cost, message="Payment amount must be greater than zero.")
            elif clean.startswith("0000") or clean.endswith("0000"):
                out = PayResult("PAYMENT_FAILED", amount=cost, message="Card declined by mock payment issuer.")
            else:
                txn = f"tx_{uuid4().hex[:12]}"
                out = PayResult("SUCCESS", txn, cost, "Payment processed successfully.")
                self.txns[txn] = out
            if req_id:
                self.charges[req_id] = out
            return out
        finally:
            self.lock.release()

    def refund(self, txn):
        self.lock.acquire()
        try:
            if not txn:
                return PayResult("NOT_FOUND", message="No payment transaction to refund.")
            already = self.refunds.get(txn)
            if already:
                return already
            original = self.txns.get(txn)
            if original is None:
                return PayResult("NOT_FOUND", message=f"Transaction {txn} not found.")
            out = PayResult(
                "SUCCESS",
                f"ref_{uuid4().hex[:12]}",
                original.amount,
                f"Refund of ${original.amount:.2f} processed for transaction {txn}.",
            )
            self.refunds[txn] = out
            return out
        finally:
            self.lock.release()
