import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
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

    @staticmethod
    def _as_decimal(value):
        if value is None:
            return None
        if isinstance(value, Decimal):
            amount = value
        else:
            text = str(value).strip()
            if text == "":
                return None
            try:
                amount = Decimal(text)
            except (InvalidOperation, TypeError, ValueError):
                return None
        if not amount.is_finite():
            return None
        return amount

    def _normalise(self, value):
        cost = self.price if value is None else self._as_decimal(value)
        if cost is None:
            return None
        return float(cost)

    def charge_count(self):
        self.lock.acquire()
        cnt = 0
        for v in self.charges.values():
            if v.status == "SUCCESS":
                cnt = cnt + 1
        self.lock.release()
        return cnt

    def txn_summary(self, txn):
        with self.lock:
            charges = 0
            amount = 0.0
            for v in self.charges.values():
                if v.status == "SUCCESS" and v.txn == txn:
                    charges = charges + 1
                    amount = v.amount
            ref = self.refunds.get(txn)
            return {
                "charges": charges,
                "amount": amount,
                "refunded": ref is not None and ref.status == "SUCCESS",
            }

    def digest_rows(self):
        with self.lock:
            rows = []
            for rid in sorted(self.charges):
                v = self.charges[rid]
                rows.append(("charge", rid, v.status, v.txn, f"{v.amount:.2f}"))
            for txn in sorted(self.refunds):
                rows.append(("refund", txn, self.refunds[txn].status))
            return rows

    def charge(self, user, amount=None, card="4242424242424242", req_id=None):
        self.lock.acquire()
        try:
            if req_id and req_id in self.charges:
                return self.charges[req_id]
            cost = self._normalise(amount)
            if cost is None or not math.isfinite(cost) or cost <= 0:
                out = PayResult(
                    "INVALID_AMOUNT", amount=0.0, message="Payment amount must be a finite number greater than zero."
                )
            elif cost < self.price:
                out = PayResult(
                    "INVALID_AMOUNT", amount=cost, message="Payment amount is below the required booking price."
                )
            else:
                clean = card.replace(" ", "").replace("-", "")
                if clean.startswith("0000") or clean.endswith("0000"):
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
        cost = self._normalise(amount)
        clean = card.replace(" ", "").replace("-", "")
        if cost is None or not math.isfinite(cost) or cost <= 0:
            return PayResult(
                "INVALID_AMOUNT", amount=0.0, message="Payment amount must be a finite number greater than zero."
            )
        if cost < self.price:
            return PayResult(
                "INVALID_AMOUNT", amount=cost, message="Payment amount is below the required booking price."
            )
        if clean.startswith("0000") or clean.endswith("0000"):
            return PayResult("PAYMENT_FAILED", amount=cost, message="Card declined by mock payment issuer.")
        return PayResult("SUCCESS", amount=cost, message="Payment can be processed.")

    def apply_charge(self, user, amount, txn, req_id):
        self.lock.acquire()
        try:
            already = self.charges.get(req_id)
            if already:
                return already
            cost = self._normalise(amount)
            if cost is None or not math.isfinite(cost) or cost <= 0 or cost < self.price:
                return PayResult("INVALID_AMOUNT", amount=0.0, message="Payment amount is not valid for this booking.")
            out = PayResult("SUCCESS", txn, cost, "Payment processed successfully.")
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
