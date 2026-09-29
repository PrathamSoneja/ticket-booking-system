from ticket_booking.payment import Payments


def test_pay_ok() -> None:
    pay = Payments(price=75.0)
    result = pay.charge("alice")
    assert result.status == "SUCCESS"
    assert result.amount == 75.0
    assert result.txn.startswith("tx_")


def test_card_decline() -> None:
    pay = Payments()
    result = pay.charge("bob", card="0000123456789012")
    assert result.status == "PAYMENT_FAILED"
    assert result.txn == ""


def test_bad_amount() -> None:
    pay = Payments()
    result = pay.charge("alice", amount=-10.0)
    assert result.status == "INVALID_AMOUNT"


def test_pay_repeat() -> None:
    pay = Payments()
    res1 = pay.charge("alice", req_id="req-123")
    res2 = pay.charge("alice", req_id="req-123")
    assert res1.status == "SUCCESS"
    assert res1.txn == res2.txn


def test_pay_refund() -> None:
    pay = Payments()
    pay_res = pay.charge("alice", req_id="req-refund")
    assert pay_res.status == "SUCCESS"
    refund_res = pay.refund(pay_res.txn)
    assert refund_res.status == "SUCCESS"
    assert refund_res.txn.startswith("ref_")
    replay = pay.refund(pay_res.txn)
    assert replay.txn == refund_res.txn
    assert pay.refund("tx_unknown").status == "NOT_FOUND"
