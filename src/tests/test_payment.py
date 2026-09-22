from ticket_booking.payment import PaymentGateway


def test_successful_payment() -> None:
    gateway = PaymentGateway(ticket_price_default=75.0)
    result = gateway.process_payment("alice")
    assert result.status_txt == "SUCCESS"
    assert result.charged_amount == 75.0
    assert result.txn_id.startswith("tx_")


def test_payment_failure_with_declined_card() -> None:
    gateway = PaymentGateway()
    result = gateway.process_payment("bob", card_num="0000123456789012")
    assert result.status_txt == "PAYMENT_FAILED"
    assert result.txn_id == ""


def test_payment_invalid_amount() -> None:
    gateway = PaymentGateway()
    result = gateway.process_payment("alice", pay_amount=-10.0)
    assert result.status_txt == "INVALID_AMOUNT"


def test_payment_idempotency() -> None:
    gateway = PaymentGateway()
    res1 = gateway.process_payment("alice", idempotency_key="req-123")
    res2 = gateway.process_payment("alice", idempotency_key="req-123")
    assert res1.status_txt == "SUCCESS"
    assert res1.txn_id == res2.txn_id


def test_payment_refund() -> None:
    gateway = PaymentGateway()
    pay_res = gateway.process_payment("alice", idempotency_key="req-refund")
    assert pay_res.status_txt == "SUCCESS"

    refund_res = gateway.refund_payment(pay_res.txn_id)
    assert refund_res.status_txt == "SUCCESS"
    assert refund_res.txn_id.startswith("ref_")

    replay = gateway.refund_payment(pay_res.txn_id)
    assert replay.txn_id == refund_res.txn_id
    assert gateway.refund_payment("tx_unknown").status_txt == "NOT_FOUND"
