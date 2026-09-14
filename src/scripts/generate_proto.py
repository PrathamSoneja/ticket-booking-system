"""Generate Python protobuf and gRPC modules from the canonical contract."""

from __future__ import annotations

from pathlib import Path

from grpc_tools import protoc


ROOT = Path(__file__).resolve().parents[1]
PROTO_DIR = ROOT / "proto"
OUTPUT_DIR = ROOT / "ticket_booking" / "generated"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "__init__.py").touch()
    result = protoc.main([
        "grpc_tools.protoc", f"-Iticket_booking/generated={PROTO_DIR}", f"--python_out={ROOT}",
        f"--pyi_out={ROOT}", f"--grpc_python_out={ROOT}",
        str(PROTO_DIR / "ticket_booking.proto"),
    ])
    if result:
        raise SystemExit(result)


if __name__ == "__main__":
    main()
