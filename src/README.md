# Distributed Ticket Booking System

From this `src` directory, activate the supplied virtual environment, install dependencies, then generate the gRPC modules:

```cmd
.venv\Scripts\activate
python -m pip install -r requirements.txt
python scripts\generate_proto.py
python -m pytest
```

`proto/ticket_booking.proto` is the authoritative wire contract. Generated files belong in `ticket_booking/generated/` and must never be hand-edited.