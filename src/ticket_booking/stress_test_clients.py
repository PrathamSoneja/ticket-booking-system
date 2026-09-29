import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from uuid import uuid4

from .client import Client

USERS = [("alice", "wonderland"), ("bob", "builder")]
i = 1
while i <= 32:
    USERS.append(("user" + str(i), "pass" + str(i)))
    i = i + 1


def browse_worker(server_addr, uname, pw):
    c = Client(server_addr)
    ok, msg = c.login(uname, pw)
    if not ok:
        c.close()
        return f"{uname}: LOGIN_FAILED"
    status, shows, _ = c.get_shows()
    seat_status, seats, _ = c.get_seats(shows[0]["show_id"]) if shows else ("NO_SHOWS", [], "")
    c.close()
    return f"{uname}: shows={len(shows)} seats={len(seats)} status={status}/{seat_status}"


def race_for_same_seat_worker(server_addr, uname, pw, show_id, seat_id):
    c = Client(server_addr)
    ok, msg = c.login(uname, pw)
    if not ok:
        c.close()
        return "LOGIN_FAILED"
    status, booking_id, message = c.book_seat(show_id, seat_id, req_id=str(uuid4()))
    c.close()
    return status


def retry_same_request_worker(server_addr, uname, pw, show_id, seat_id, shared_req_id):
    c = Client(server_addr)
    ok, msg = c.login(uname, pw)
    if not ok:
        c.close()
        return "LOGIN_FAILED"
    status, booking_id, message = c.book_seat(show_id, seat_id, req_id=shared_req_id)
    c.close()
    return (status, booking_id)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="127.0.0.1:50051")
    parser.add_argument("--race-workers", type=int, default=15)
    parser.add_argument("--show-id", default="show-1")
    parser.add_argument("--seat-id", default="A1")
    args = parser.parse_args()

    print("=== Phase 1: concurrent browsing by different users ===")
    with ThreadPoolExecutor(max_workers=10) as pool:
        futs = [
            pool.submit(browse_worker, args.server, uname, pw)
            for _ in range(10)
            for uname, pw in [USERS[_ % len(USERS)]]
        ]
        for f in as_completed(futs):
            print(f.result())

    print(f"\n=== Phase 2: {args.race_workers} threads racing for the SAME seat "
          f"({args.show_id}/{args.seat_id}) ===")
    with ThreadPoolExecutor(max_workers=args.race_workers) as pool:
        futs = [
            pool.submit(
                race_for_same_seat_worker,
                args.server,
                USERS[i % len(USERS)][0],
                USERS[i % len(USERS)][1],
                args.show_id,
                args.seat_id,
            )
            for i in range(args.race_workers)
        ]
        results = [f.result() for f in as_completed(futs)]

    tally = Counter(results)
    print("Outcome tally:", dict(tally))
    print("Expected: exactly 1x 'OK', rest 'ALREADY_BOOKED' -> "
          f"{'PASS' if tally.get('OK', 0) == 1 else 'CHECK MANUALLY'}")

    print(f"\n=== Phase 3: retrying the SAME request_id (idempotency check) ===")
    shared_req_id = str(uuid4())
    with ThreadPoolExecutor(max_workers=5) as pool:
        futs = [
            pool.submit(
                retry_same_request_worker,
                args.server,
                "alice",
                "wonderland",
                args.show_id,
                "A2",
                shared_req_id,
            )
            for _ in range(5)
        ]
        retry_results = [f.result() for f in as_completed(futs)]

    unique_booking_ids = {bid for (status, bid) in retry_results if status == "OK"}
    print("Retry results:", retry_results)
    print("Expected: all identical booking_id despite 5 retries -> "
          f"{'PASS' if len(unique_booking_ids) <= 1 else 'FAIL (duplicate bookings!)'}")


if __name__ == "__main__":
    main()
