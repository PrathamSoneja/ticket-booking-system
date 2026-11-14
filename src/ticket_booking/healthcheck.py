import sys

import grpc

from .generated import ticket_booking_pb2 as pb
from .generated import ticket_booking_pb2_grpc as pb_grpc


def check_raft(addr, timeout=2.0):
    with grpc.insecure_channel(addr) as ch:
        reply = pb_grpc.RaftServiceStub(ch).RequestVote(
            pb.RequestVoteArgs(**{"from": "healthcheck", "term": 0}), timeout=timeout
        )
    return not reply.vote_granted


def check_llm(addr, timeout=2.0):
    with grpc.insecure_channel(addr) as ch:
        grpc.channel_ready_future(ch).result(timeout=timeout)
    return True


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] not in ("raft", "llm"):
        print("usage: python -m ticket_booking.healthcheck raft|llm host:port", file=sys.stderr)
        return 2
    kind, addr = argv
    try:
        ok = check_raft(addr) if kind == "raft" else check_llm(addr)
    except (grpc.RpcError, grpc.FutureTimeoutError) as err:
        print(f"unhealthy: {kind} {addr}: {type(err).__name__}", file=sys.stderr)
        return 1
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
