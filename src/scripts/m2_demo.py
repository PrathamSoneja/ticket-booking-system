import argparse
import socket
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Fix Python imports BEFORE importing ticket_booking
SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))

from ticket_booking.client import Client

PYTHON = sys.executable
PASSWORD = "PassDemo123"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_port(port, timeout=20):
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.25)

    return False


def start_node(node_id, port, peers, data_dir, log_dir):
    log = open(
        log_dir / f"{node_id}.log",
        "w",
        encoding="utf-8",
    )

    command = [
        PYTHON,
        "-m",
        "ticket_booking.server",
        "--node-id",
        node_id,
        "--port",
        str(port),
        "--peers",
        peers,
        "--data-dir",
        str(data_dir / node_id),
    ]

    process = subprocess.Popen(
        command,
        cwd=SRC,
        stdout=log,
        stderr=subprocess.STDOUT,
    )

    return process, log


def stop_process(process):
    if process and process.poll() is None:
        process.terminate()

        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def get_status(address, token):
    client = Client(address)

    try:
        client.token = token
        status, details, message = client.get_cluster_status()

        if status == "OK":
            return details

        return None

    except Exception:
        return None

    finally:
        client.close()


def find_leader(addresses, token, excluded=None, timeout=25):
    deadline = time.time() + timeout

    while time.time() < deadline:
        for node_id, address in addresses.items():
            if node_id == excluded:
                continue

            details = get_status(address, token)

            if details and details.get("role") == "leader":
                return node_id, details

        time.sleep(0.5)

    return None, None


def signup_and_book(address, username, show_id, seat_id):
    client = Client(address)

    try:
        ok, message = client.signup(username, PASSWORD)

        if not ok:
            return "SIGNUP_FAILED", message

        status, booking_id, message = client.book_seat(
            show_id,
            seat_id,
        )

        return status, booking_id or message

    except Exception as error:
        return "ERROR", str(error)

    finally:
        client.close()


def print_logs(log_dir):
    print("\n=== NODE LOGS (LAST 30 LINES) ===")

    for node in ["node-1", "node-2", "node-3"]:
        path = log_dir / f"{node}.log"

        if path.exists():
            print(f"\n--- {node} ---")

            try:
                lines = path.read_text(
                    encoding="utf-8",
                    errors="replace",
                ).splitlines()

                for line in lines[-30:]:
                    print(line)

            except Exception as error:
                print("Could not read log:", error)


def main():
    parser = argparse.ArgumentParser(description="End-to-end three-node Raft M2 demonstration")

    parser.add_argument(
        "--workers",
        type=int,
        default=10,
    )

    parser.add_argument(
        "--skip-faq",
        action="store_true",
    )

    args = parser.parse_args()

    if args.workers < 2 or args.workers > 30:
        parser.error("--workers must be between 2 and 30")

    processes = {}
    logs = {}

    log_dir = SRC / "scripts" / "m2_demo_logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="ticket_m2_") as temporary:
        root = Path(temporary)
        data_dir = root / "data"
        data_dir.mkdir()

        ports = [free_port() for _ in range(3)]

        node_ids = [
            "node-1",
            "node-2",
            "node-3",
        ]

        addresses = {node: f"127.0.0.1:{port}" for node, port in zip(node_ids, ports, strict=False)}

        peers = ",".join(f"{node}={addresses[node]}" for node in node_ids)

        token = None

        try:
            print("\n=== PHASE 1: START THREE RAFT NODES ===")

            for node, port in zip(node_ids, ports, strict=False):
                process, log = start_node(
                    node,
                    port,
                    peers,
                    data_dir,
                    log_dir,
                )

                processes[node] = process
                logs[node] = log

            for node, port in zip(node_ids, ports, strict=False):
                if not wait_port(port):
                    raise RuntimeError(f"{node} did not start on port {port}")

                print(f"PASS: {node} listening on port {port}")

            print("Waiting for initial leader election...")
            time.sleep(5)

            print("\n=== PHASE 2: CONCURRENT SAME-SEAT BOOKING ===")

            print(f"Testing {args.workers} users against show-1/A1")

            results = []

            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = [
                    pool.submit(
                        signup_and_book,
                        addresses["node-1"],
                        f"m2demo{i:02d}",
                        "show-1",
                        "A1",
                    )
                    for i in range(1, args.workers + 1)
                ]

                for future in as_completed(futures):
                    result = future.result()
                    results.append(result)
                    print(result)

            successes = sum(1 for status, _ in results if status == "OK")

            conflicts = sum(1 for status, _ in results if status == "ALREADY_BOOKED")

            if successes != 1 or conflicts != args.workers - 1:
                raise RuntimeError("Concurrency check failed: " f"{successes} successes, " f"{conflicts} conflicts")

            print("PASS: exactly one user booked A1")

            username = "m2demo01"

            client = Client(addresses["node-1"])

            ok, message = client.login(
                username,
                PASSWORD,
            )

            if not ok:
                raise RuntimeError(f"Login failed: {message}")

            token = client.token

            print("\n=== PHASE 3: CLUSTER STATUS ===")

            leader_id, details = find_leader(
                addresses,
                token,
            )

            if not leader_id:
                raise RuntimeError("No Raft leader found")

            print(f"PASS: leader is {leader_id}")
            print("Leader status:", details)

            print("\n=== PHASE 4: FAQ CHECK ===")

            if not args.skip_faq:
                try:
                    answer = client.ask_faq(
                        "How do I book a seat?",
                        show_id="show-1",
                    )

                    if answer and not answer.startswith("FAQ failed"):
                        print("FAQ response:", answer)
                        print("PASS: FAQ request completed")
                    else:
                        print(
                            "WARN: FAQ service unavailable:",
                            answer,
                        )

                except Exception as error:
                    print(
                        "WARN: FAQ check could not complete:",
                        error,
                    )

            else:
                print("SKIPPED: --skip-faq specified")

            client.close()

            print("\n=== PHASE 5: KILL CURRENT LEADER ===")

            failed_node = leader_id

            stop_process(processes[failed_node])

            print(f"Stopped {failed_node}")

            new_leader, new_details = find_leader(
                addresses,
                token,
                excluded=failed_node,
                timeout=30,
            )

            if not new_leader:
                raise RuntimeError("No replacement leader elected after failure")

            print(f"PASS: replacement leader is {new_leader}")

            print("New leader status:", new_details)

            print("\n=== PHASE 6: BOOK AFTER FAILOVER ===")

            survivor = addresses[new_leader]

            client = Client(survivor)

            ok, message = client.login(
                username,
                PASSWORD,
            )

            if not ok:
                raise RuntimeError(f"Login after failover failed: {message}")

            status, booking_id, message = client.book_seat(
                "show-1",
                "A2",
            )

            print(
                "Post-failover booking:",
                status,
                booking_id,
                message,
            )

            if status != "OK":
                raise RuntimeError("Booking after failover failed: " f"{status} {message}")

            print("PASS: new booking committed after leader failure")

            client.close()

            leader_status = get_status(
                survivor,
                token,
            )

            target_commit = leader_status.get("commit_index", 0) if leader_status else 0

            print("\n=== PHASE 7: RESTART FAILED NODE ===")

            failed_port = int(addresses[failed_node].rsplit(":", 1)[1])

            process, log = start_node(
                failed_node,
                failed_port,
                peers,
                data_dir,
                log_dir,
            )

            processes[failed_node] = process
            logs[failed_node] = log

            if not wait_port(failed_port):
                raise RuntimeError("Restarted node did not become reachable")

            caught_up = False
            deadline = time.time() + 30

            while time.time() < deadline:
                recovered = get_status(
                    addresses[failed_node],
                    token,
                )

                if recovered:
                    applied = recovered.get(
                        "last_applied",
                        0,
                    )

                    if recovered.get("role") == "follower" and applied >= target_commit:
                        caught_up = True

                        print(
                            "Recovered node status:",
                            recovered,
                        )

                        break

                time.sleep(0.5)

            if not caught_up:
                raise RuntimeError("Restarted node did not catch up " f"to committed index {target_commit}")

            print("PASS: restarted node caught up " "with committed entries")

            print("\n=== M2 DEMO RESULT ===")
            print("PASS: three-node cluster startup")
            print("PASS: concurrent booking safety")
            print("PASS: leader election")
            print("PASS: leader failure and re-election")
            print("PASS: write after failover")
            print("PASS: restarted follower catch-up")

            print("\nALL CORE M2 DEMO CHECKS PASSED")

        except Exception:
            print("\nDEMO FAILED. CHECKING NODE LOGS...")
            print_logs(log_dir)
            raise

        finally:
            for node in node_ids:
                stop_process(processes.get(node))

            for log in logs.values():
                log.close()


if __name__ == "__main__":
    main()
