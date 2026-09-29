import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1]
GENERATED_DIR = SRC_DIR / "ticket_booking" / "generated"
if str(GENERATED_DIR) not in sys.path:
    sys.path.insert(0, str(GENERATED_DIR))


def test_proto_import() -> None:
    import ticket_booking_pb2
    import ticket_booking_pb2_grpc

    assert hasattr(ticket_booking_pb2, "LoginRequest")
    assert hasattr(ticket_booking_pb2, "LoginResponse")
    assert hasattr(ticket_booking_pb2, "PostRequest")
    assert hasattr(ticket_booking_pb2, "StatusResponse")
    assert hasattr(ticket_booking_pb2, "LLMRequest")
    assert hasattr(ticket_booking_pb2, "LLMResponse")
    assert hasattr(ticket_booking_pb2_grpc.ClientServiceServicer, "Signup")
    assert hasattr(ticket_booking_pb2_grpc, "ClientServiceServicer")
    assert hasattr(ticket_booking_pb2_grpc, "RaftServiceServicer")
    assert hasattr(ticket_booking_pb2_grpc, "LLMServiceServicer")


