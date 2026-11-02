from dataclasses import dataclass
from threading import Lock
from uuid import NAMESPACE_URL, uuid4, uuid5


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

    def check(self, amount=None, card="4242424242424242"):
        cost = self.price if amount is None else amount
        clean = card.replace(" ", "").replace("-", "")
        if cost <= 0:
            return PayResult("INVALID_AMOUNT", amount=cost, message="Payment amount must be greater than zero.")
        if clean.startswith("0000") or clean.endswith("0000"):
            return PayResult("PAYMENT_FAILED", amount=cost, message="Card declined by mock payment issuer.")
        return PayResult("SUCCESS", amount=cost, message="Payment can be processed.")

    def apply_charge(self, user, amount, txn, req_id):
        self.lock.acquire()
        try:
            already = self.charges.get(req_id)
            if already:
                return already
            out = PayResult("SUCCESS", txn, amount, "Payment processed successfully.")
            self.charges[req_id] = out
            self.txns[txn] = out
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
            ref = f"ref_{uuid5(NAMESPACE_URL, 'ticket-booking/refund/' + txn).hex[:12]}"
            out = PayResult(
                "SUCCESS",
                ref,
                original.amount,
                f"Refund of ${original.amount:.2f} processed for transaction {txn}.",
            )
            self.refunds[txn] = out
            return out
        finally:
            self.lock.release()
