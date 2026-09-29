# Distributed Ticket Booking System

This system is intended to serve as a dependable platform for booking tickets concurrently. It demonstrates how clients and servers communicate through gRPC, how in-memory state is managed in a thread safe manner using a mutex, and how shows and FAQs are stored persistently in SQLite. Payment handling is carried out with deterministic mock logic, the cluster leader is redirected in a transparent way, and questions posed in natural language are answered by a locally hosted language model.

## 1. Main Features and Technologies

Main features:

- User registration and authentication with password hashing and expiring session tokens
- Browse show schedules and real-time seat availability
- Multi-seat reservations up to 5 seats per request
- Concurrent booking protection using ordered per-seat locks
- Idempotent request handling
- Mock payment and refund handling
- Context-aware support chat and an Ollama LLM service
- Client redirection from follower nodes to the leader node
- Automated unit, integration, and concurrency stress tests

Technologies:

- Python 3.13
- gRPC 
- Protocol Buffers 
- SQLite3 
- Ollama
- NLTK 3.10.3
- Pytest 8.4.2



## 2. How the Client, Server, Database, and Ticket Booking Flow Work

System architecture:

- Client: Through gRPC stubs, a Python command-line interface talks to the server. The session token is kept after login, and when write requests land on follower nodes, the client follows the leader redirection on its own.
- Server: Client requests are handled by an application server that runs ClientService, which relies on a thread pool. It checks that session tokens are valid and that requests are idempotent, carries out seat allocations, and works with the mock payment gateway as well as the LLM service.
- Database: Static show schedules and support FAQ topics sit in an embedded SQLite database (catalog. db). If the tables are empty when the process starts, seed data is loaded into SQLite.
- LLM Service: A separate gRPC service (LLMService) finds matching FAQ documents through keyword overlap and NLTK stopwords, takes live seat availability from the application server, and calls Ollama at [http://127.0.0.1:11434/api/generate](http://127.0.0.1:11434/api/generate).

Ticket booking flow:

1. The client is where the user logs in or signs up. Once the credentials pass verification on the server side, a 32-byte session token comes back in the response.
2. A request then goes out for the available shows, or for the seats at one particular show. Catalog data and the live seat map are both queried by the server.
3. For a booking, the user sends in a request that names the show ID and the seat IDs, along with the payment card details.
  Should the request land on a follower node, that follower answers with status NOT_LEADER and passes back the leader address. The client then reconnects to the leader.
4. At the leader, the session token is validated, and a check is made as to whether that request ID has been dealt with before.
5. The leader goes through every requested seat in sorted order and takes out locks on them, which is what keeps deadlocks from arising.
6. The leader verifies that all requested seats are currently AVAILABLE. If any seat is already booked, the request fails with status ALREADY_BOOKED.
7. If seats are available, the leader processes the mock payment. Cards starting or ending with 0000 are declined with PAYMENT_FAILED, leaving seats unreserved.
8. Upon successful payment, seats transition to BOOKED status with the user ID and generated booking ID. The booking record is saved and the result is returned to the client.
9. If the user cancels a booking, the leader verifies ownership, marks seats back to AVAILABLE, sets booking status to CANCELLED, and issues an automatic refund.



## 3. Requirements

System requirements:

- Operating System: Windows
- Python: Version 3.13
- Git
- Ollama



## 4. Complete Project Setup

Run from inside the src directory.

Windows (PowerShell):

```powershell
cd src
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python scripts\generate_proto.py
python -c "import nltk; nltk.download('stopwords')"
```

LLM setup for FAQ service:

1. Install Ollama from [https://ollama.com](https://ollama.com)
2. Pull the required language model:

```bash
ollama pull llama3.2:1b
```

Keep the Ollama application or daemon running in the background.

## 5. Server Installation and Start Command

Step 1: Start the LLM FAQ service (Terminal 1):

```bash
python -m ticket_booking.llm_service
```

This runs the FAQ gRPC service on port 50055.

Step 2: Start the application server (Terminal 2):

```bash
python -m ticket_booking.server
```

This starts the leader application server on port 50051.

(Optional) follower node setup: To run an additional follower node that redirects writes to the leader:

```bash
python -m ticket_booking.server --follower --leader 127.0.0.1:50051 --port 50052
```

Available server command-line options:

- --port: Listening port for the application server (default: 50051)
- --llm-server: Address of the LLM service (default: 127.0.0.1:50055)
- --follower: Run in follower mode (redirects write requests to leader)
- --leader: Address of cluster leader to provide in redirect responses



## 6. Client Installation and Start Command

Run the commands from the src directory in the virtual environment.

List shows:

```bash
python -m ticket_booking.client --action shows
```

List seats for a show:

```bash
python -m ticket_booking.client --action seats --show-id show-1
```

Book a single seat:

```bash
python -m ticket_booking.client --action book --show-id show-1 --seat-id A1
```

Book multiple seats (up to 5 seats):

```bash
python -m ticket_booking.client --action book --show-id show-1 --seat-id A1,A2,A3
```

Cancel a booking:

```bash
python -m ticket_booking.client --action cancel --booking-id <booking_id_from_booking>
```

Ask an FAQ support question:

```bash
python -m ticket_booking.client --action faq --query "How do I cancel a booking?"
```

Register a new user account:

```bash
python -m ticket_booking.client --action signup --username charlie --password GoodPass1
```

Client command-line options:

- --server: Target server address (default: 127.0.0.1:50051)
- --username: Username for authentication (default: alice)
- --password: Password for authentication (default: wonderland)
- --action: Operation to execute (signup, shows, seats, book, cancel, faq; default: shows)
- --show-id: Show identifier for seats, bookings, or FAQs (default: show-1)
- --seat-id: Seat identifier or comma-separated list of up to 5 seats (default: A1)
- --booking-id: Booking identifier used for cancellations
- --query: Support question string for FAQ requests



## 7. Database Setup

Database setup:

- Database engine: SQLite3
- Database location: src/ticket_booking/catalog.db
- Initialization: src/ticket_booking/seed.py.
- Seed data:
  - 8 shows (show-1 through show-8), each initialized with 50 seats (A1 to A50).
  - 6 policy and FAQ documents covering cancellation rules, refund policies, booking procedures, advance booking windows, seat change policies, and seat availability.
- Default user credentials:
  - alice / wonderland
  - bob / builder
  - user1 through user32 with passwords pass1 through pass32



## 8. How Clients Send Requests to the Server

Communication protocol:

- Clients connect to the server using gRPC over HTTP/2 with binary Protocol Buffers serialization.
- REST and traditional HTTP clients (such as standard curl, browser URLs, or standard Swagger OpenAPI endpoints) are not supported directly because the endpoints are binary gRPC services.
- If using an external API tool like Postman or grpcurl, select gRPC request mode and import src/proto/ticket_booking.proto to interact with the service methods.

Request handling:

- Session tokens: The client calls Login or Signup to obtain an authentication token, which must be passed in all subsequent Get and Post requests.
- Redirection: When a write request (Post) is sent to a follower node, the follower replies with status NOT_LEADER and provides the leader address in redirect_to. The client closes the follower connection, opens a channel to the leader, and repeats the request.



## 9. Testing Instructions

From the src directory in virtual environment:

```bash
python -m pytest
```

Running individual test suites:

```bash
python -m pytest tests/test_proto.py
python -m pytest tests/test_auth.py
python -m pytest tests/test_booking.py
python -m pytest tests/test_payment.py
python -m pytest tests/test_client.py
python -m pytest tests/test_llm.py
python -m pytest tests/test_integration.py
```

Note regarding LLM tests:
Tests in test_llm.py and the test_cancel_faq test in test_integration.py require Ollama to be running with the llama3.2:1b model pulled. If Ollama is offline, LLM tests report UNAVAILABLE.

Running multi-threaded stress and race condition tests:
From the src directory with the server running:

```bash
python -m ticket_booking.stress_test_clients --server 127.0.0.1:50051 --race-workers 15
```

This script runs three phases:

- Concurrent browsing across multiple user sessions
- 15 concurrent threads racing to book the exact same seat
- Concurrent retries with identical request_id



## 10. Team Contributions

All three members of the team contributed equally in the development, testing, debugging, documentation, deployment and final completion of this project.

Pratham Soneja:

- Backend and State Machine: Developed the in-memory seat allocation state machine, fine-grain seat locking mechanism and multi-seat transaction handling.
- Concurrency and Testing: Built tools for concurrency stress testing and unit test suites to test race conditions and idempotent replay of requests.
- Client and API Routing: Built the command line client interface, session token lifecycle and the follower to leader transparent redirection logic.

Rathul Krishnan:

- Developed end-to-end integration tests with gRPC channels for multi-user browsing, booking and cancelation flows.
- System Debugging: Fixed thread sync edge cases, tested gRPC channel reliability under multi-connection loads.
- Deployment & Documentation: Organized virtual environment setup, dependency configuration, and project documentation.

Mitali Narayan:

- LLM Service and Database: Built gRPC LLM service, Ollama integration and SQLite database catalog schema with automated seeding for shows and FAQ entries.
- Retrieval Engine and Payments: NLTK keyword scoring for domain FAQ retrieval, deterministic mock payment and refund handling.
- Project Test and Collaboration: Synchronized test suite execution between components, checked API contract adherence to protobuf definitions, helped with documentation..

