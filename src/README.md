# Distributed Ticket Booking System

Course project (CS G623): users log in, browse shows, book or cancel seats, and ask booking FAQs. Services talk over gRPC.

**Now (Milestone 1):** one app server, a separate FAQ/LLM server, a CLI client, mock payments, and session auth. Two people cannot book the same seat on that node.

**Later (Milestone 2):** three app servers keep the same seat map using Raft. That is not implemented yet. The Raft RPCs exist in the proto file only.

Demo users: `alice` / `wonderland`, `bob` / `builder`.

---

## How to run

Work from this `src` folder. Use a virtual environment (do not install packages globally).

**Windows (PowerShell)**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python scripts\generate_proto.py
python -c "import nltk; nltk.download('stopwords')"
python -m pytest
```

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/generate_proto.py
python -c "import nltk; nltk.download('stopwords')"
python -m pytest
```

`proto/ticket_booking.proto` is the API contract. Generated Python lives in `ticket_booking/generated/` — do not edit those files by hand. Re-run `generate_proto.py` after changing the proto.

### FAQ / Ollama (needed only for live FAQ, not for most tests)

1. Install [Ollama](https://ollama.com).
2. `ollama pull llama3.2:1b`
3. Leave Ollama running.

If Ollama is down, FAQ calls return `LLM_UNAVAILABLE`. Tests stub the model and do not need Ollama.

### Start the servers, then the client

Three terminals, venv activated in each, from `src`:

```powershell
python -m ticket_booking.llm_service
```

```powershell
python -m ticket_booking.server
```

Default ports: LLM `50055`, app `50051`. Point the app at a different LLM with `--llm-server host:port`. Run a non-leader app (redirects writes) with `--follower --leader 127.0.0.1:50051 --port 50052`.

```powershell
python -m ticket_booking.client --action shows
python -m ticket_booking.client --action seats --show-id show-1
python -m ticket_booking.client --action book --show-id show-1 --seat-id A1
python -m ticket_booking.client --action cancel --booking-id <id from book>
python -m ticket_booking.client --action faq --query "How do I cancel a booking?"
```

Optional: `--server`, `--username`, `--password`.

---

## What each file does

### Config and build

| File | Role |
|---|---|
| `requirements.txt` | Python packages (`grpcio`, `pytest`, `nltk`, …). |
| `pyproject.toml` | Project metadata and pytest paths. |
| `scripts/generate_proto.py` | Compiles the `.proto` file into Python gRPC stubs. |
| `proto/ticket_booking.proto` | Login, browse, book, Raft, and LLM RPC shapes. |

### Application code (`ticket_booking/`)

| File | Role |
|---|---|
| `__init__.py` | Marks this folder as a Python package. |
| `auth.py` | Login/logout and expiring session tokens. |
| `state_machine.py` | Shows, seats, book, cancel. One lock so two bookings cannot take the same seat. Same `request_id` is not applied twice. |
| `payment.py` | Fake payment and refund. Cards starting or ending with `0000` fail. |
| `application.py` | Ties auth, seats, and payment together for get/post. |
| `server.py` | gRPC app server (`ClientService`). Forwards FAQ to the LLM server. Followers reply `NOT_LEADER`. |
| `client.py` | CLI that logs in and calls the app server. Follows leader redirects on writes. |
| `faq_data.py` | Small FAQ knowledge base and keyword lookup for RAG-style answers. |
| `llm_service.py` | Separate gRPC FAQ process. Looks up FAQ text, then calls local Ollama. |
| `generated/` | Auto-generated protobuf/gRPC code. |

### Tests (`tests/`)

| File | Role |
|---|---|
| `test_proto.py` | Generated stubs import cleanly. |
| `test_auth.py` | Login, logout, expired token. |
| `test_booking.py` | Concurrent book, cancel, token required. |
| `test_payment.py` | Charge, decline, refund, idempotency. |
| `test_llm.py` | FAQ answers and missing Ollama. |
| `test_client.py` | CLI against a local cluster, including redirect. |
| `test_integration.py` | Full gRPC path: concurrent book, payment fail, cancel + FAQ. |
