


from ticket_booking.payment import PaymentGateway


def test_successful_payment() -> None:
    gateway = PaymentGateway(default_ticket_price=75.0)
    result = gateway.process_payment("alice")
    assert result.status == "SUCCESS"
    assert result.amount == 75.0
    assert result.transaction_id.startswith("tx_")


def test_payment_failure_with_declined_card() -> None:
    gateway = PaymentGateway()
    result = gateway.process_payment("bob", card_number="0000123456789012")
    assert result.status == "PAYMENT_FAILED"
    assert result.transaction_id == ""


def test_payment_invalid_amount() -> None:
    gateway = PaymentGateway()
    result = gateway.process_payment("alice", amount=-10.0)
    assert result.status == "INVALID_AMOUNT"


def test_payment_idempotency() -> None:
    gateway = PaymentGateway()
    res1 = gateway.process_payment("alice", idempotency_key="req-123")
    res2 = gateway.process_payment("alice", idempotency_key="req-123")
    assert res1.status == "SUCCESS"
    assert res1.transaction_id == res2.transaction_id


def test_payment_refund() -> None:
    gateway = PaymentGateway()
    pay_res = gateway.process_payment("alice", idempotency_key="req-refund")
    assert pay_res.status == "SUCCESS"

    refund_res = gateway.refund_payment(pay_res.transaction_id)
    assert refund_res.status == "SUCCESS"
    assert refund_res.transaction_id.startswith("ref_")

    replay = gateway.refund_payment(pay_res.transaction_id)
    assert replay.transaction_id == refund_res.transaction_id
    assert gateway.refund_payment("tx_unknown").status == "NOT_FOUND"


