import argparse
import json
import logging
import sys
from concurrent import futures
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_urlsafe
from threading import Lock

import grpc

GEN = Path(__file__).resolve().parent / "generated"
if str(GEN) not in sys.path:
    sys.path.insert(0, str(GEN))

import ticket_booking_pb2 as pb
import ticket_booking_pb2_grpc as pb_grpc
from ticket_booking.application import BookingApp
from ticket_booking.raft_node import RaftNode
from ticket_booking.raft_service import RaftService

log = logging.getLogger("ticket_booking")


class AppServer(pb_grpc.ClientServiceServicer):
    def __init__(self, app=None, pay=None, is_leader=True, leader_url="", llm_url="", raft=None):
        if app:
            self.app = app
        else:
            self.app = BookingApp(pay=pay)
        if pay:
            self.app.pay = pay
        self.is_leader = is_leader
        self.raft = raft
        self.leader_url = leader_url
        self.signup_lock = Lock()
        if llm_url:
            self.llm_ch = grpc.insecure_channel(llm_url)
            self.llm = pb_grpc.LLMServiceStub(self.llm_ch)
        else:
            self.llm_ch = None
            self.llm = None

    def close(self):
        if self.llm_ch:
            self.llm_ch.close()

    def Login(self, req, ctx):
        if self.raft:
            if self.raft.role != "leader":
                return self._forward_auth("Login", req)
            if not self.app.auth.check_credentials(req.username, req.password):
                return pb.LoginResponse(status="AUTH_FAILED", message="Invalid username or password.")
            tok = token_urlsafe(32)
            exp = datetime.now(UTC) + self.app.auth.lifetime
            key = self.app.auth.token_key(tok)
            body = json.dumps(
                {"token_hash": key, "user_id": req.username, "expiry": exp.isoformat()},
                separators=(",", ":"),
            ).encode()
            status, _ = self.raft.propose("AUTH_SESSION", body, "session:" + key)
            if status != "OK":
                return pb.LoginResponse(status=status, message="Login could not reach a Raft majority.")
            return pb.LoginResponse(status="OK", token=tok, message="Logged in.")
        out = self.app.login(req.username, req.password)
        return pb.LoginResponse(status=out.status, token=out.token, message=out.message)

    def Signup(self, req, ctx):
        if self.raft:
            if self.raft.role != "leader":
                return self._forward_auth("Signup", req)
            with self.signup_lock:
                status, name, digest, msg = self.app.auth.prepare_signup(req.username, req.password)
                if status != "OK":
                    return pb.LoginResponse(status=status, message=msg)
                tok = token_urlsafe(32)
                exp = datetime.now(UTC) + self.app.auth.lifetime
                key = self.app.auth.token_key(tok)
                body = json.dumps(
                    {
                        "user_id": name,
                        "password_hash": digest.hex(),
                        "token_hash": key,
                        "expiry": exp.isoformat(),
                    },
                    separators=(",", ":"),
                ).encode()
                status, _ = self.raft.propose("AUTH_SIGNUP", body, "signup:" + key)
                if status != "OK":
                    return pb.LoginResponse(
                        status=status, message="Signup could not reach a Raft majority."
                    )
                return pb.LoginResponse(status="OK", token=tok, message="Account registered.")
        out = self.app.signup(req.username, req.password)
        return pb.LoginResponse(status=out.status, token=out.token, message=out.message)

    def Logout(self, req, ctx):
        if self.raft:
            if self.raft.role != "leader":
                return self._forward_auth("Logout", req)
            if self.app.auth.token_user(req.token) is None:
                return pb.StatusResponse(status="AUTH_FAILED", message="Invalid or expired session.")
            key = self.app.auth.token_key(req.token)
            body = json.dumps({"token_hash": key}, separators=(",", ":")).encode()
            status, _ = self.raft.propose("AUTH_LOGOUT", body, "logout:" + key)
            if status != "OK":
                return pb.StatusResponse(
                    status=status, message="Logout could not reach a Raft majority."
                )
            return pb.StatusResponse(status="OK", message="Logged out.")
        out = self.app.logout(req.token)
        return pb.StatusResponse(status=out.status, message=out.message)

    def Get(self, req, ctx):
        raw = req.params
        if not raw:
            data = {}
            err = ""
        else:
            try:
                data = json.loads(raw.decode())
                if not isinstance(data, dict):
                    data = None
                    err = "Request payload must be a JSON object."
                else:
                    err = ""
            except (UnicodeDecodeError, json.JSONDecodeError):
                data = None
                err = "Request payload is not valid JSON."
        if err:
            return pb.GetResponse(status="INVALID_REQUEST", message=err)
        params = {}
        for k in data:
            params[str(k)] = str(data[k])
        if req.type == "CLUSTER_STATUS":
            if self.app.auth.token_user(req.token) is None:
                return pb.GetResponse(
                    status="AUTH_FAILED", message="A valid session token is required."
                )
            if self.raft:
                status = self.raft.status()
            else:
                status = {
                    "node_id": "single-node",
                    "role": "leader" if self.is_leader else "follower",
                    "term": 0,
                    "leader_id": self.leader_url,
                    "log_length": 0,
                    "commit_index": 0,
                    "last_applied": 0,
                }
            return pb.GetResponse(status="OK", message=json.dumps(status))
        status, items, msg = self.app.get(req.token, req.type, params)
        if status != "OK":
            return pb.GetResponse(status=status, message=msg)
        if req.type == "FAQ":
            q = params.get("query", "").strip()
            if not q:
                return pb.GetResponse(status="INVALID_REQUEST", message="FAQ requests require a query parameter.")
            if self.llm is None:
                return pb.GetResponse(status="LLM_UNAVAILABLE", message="The FAQ service is not configured.")
            try:
                ctx_str = self.app.faq_context(q, params)
                ans = self.llm.GetLLMAnswer(
                    pb.LLMRequest(request_id=req.token, query=q, context=ctx_str), timeout=60
                ).answer
            except grpc.RpcError as err:
                log.warning("LLM RPC failed: %s", err)
                return pb.GetResponse(status="LLM_UNAVAILABLE", message="The FAQ service could not be reached.")
            return pb.GetResponse(status="OK", message=ans)
        proto_items = []
        for it in items:
            proto_items.append(pb.Item(id=it.get("show_id") or it.get("seat_id") or "", data=json.dumps(it).encode()))
        return pb.GetResponse(status=status, items=proto_items, message=msg)

    def Post(self, req, ctx):
        if self.raft and self.raft.role != "leader":
            leader = self.raft.leader_address or ""
            return pb.StatusResponse(
                status="NOT_LEADER",
                message="Redirect to current cluster leader.",
                redirect_to=leader,
            )
        if not self.raft and not self.is_leader:
            return pb.StatusResponse(
                status="NOT_LEADER", message="Redirect to current cluster leader.", redirect_to=self.leader_url
            )
        raw = req.data
        if not raw:
            data = {}
            err = ""
        else:
            try:
                data = json.loads(raw.decode())
                if not isinstance(data, dict):
                    data = None
                    err = "Request payload must be a JSON object."
                else:
                    err = ""
            except (UnicodeDecodeError, json.JSONDecodeError):
                data = None
                err = "Request payload is not valid JSON."
        if err:
            return pb.StatusResponse(status="INVALID_REQUEST", message=err)
        clean = {}
        for k in data:
            v = data[k]
            if v is None:
                clean[str(k)] = ""
            else:
                clean[str(k)] = str(v)
        if self.raft:
            out = self.app.raft_post(req.token, req.type, clean, req.request_id, self.raft)
        else:
            out = self.app.post(req.token, req.type, clean, req.request_id)
        return pb.StatusResponse(status=out.status, message=out.message, booking_id=out.booking_id)

    def _forward_auth(self, method, req):
        response = pb.StatusResponse if method == "Logout" else pb.LoginResponse
        leader = self.raft.leader_address or ""
        if not leader:
            self.raft.wait_for_leader(1.0)
            leader = self.raft.leader_address or ""
        if not leader:
            return response(status="NO_LEADER", message="The cluster has not elected a leader.")
        ch = grpc.insecure_channel(leader)
        stub = pb_grpc.ClientServiceStub(ch)
        try:
            call = getattr(stub, method)
            res = call(req, timeout=3.0)
        except grpc.RpcError as err:
            log.warning("Could not forward %s to cluster leader: %s", method, err)
            return response(status="NO_LEADER", message="The cluster leader is unavailable.")
        finally:
            ch.close()
        return res


def serve(
    app=None,
    pay=None,
    port=50051,
    is_leader=True,
    leader_url="",
    llm_url="",
    workers=64,
    bind="[::]",
    raft=None,
):
    srv = grpc.server(futures.ThreadPoolExecutor(max_workers=workers))
    pb_grpc.add_ClientServiceServicer_to_server(
        AppServer(app, pay, is_leader, leader_url, llm_url, raft), srv
    )
    if raft:
        pb_grpc.add_RaftServiceServicer_to_server(RaftService(raft), srv)
    bound = srv.add_insecure_port(f"{bind}:{port}")
    srv.start()
    if raft:
        raft.start()
    return srv, bound


def parse_peers(raw):
    peers = {}
    for item in raw.split(","):
        bit = item.strip()
        if bit == "":
            continue
        if "=" not in bit:
            raise ValueError("each peer must use node-id=host:port")
        node, addr = bit.split("=", 1)
        if not node or not addr or node in peers:
            raise ValueError("peer IDs and addresses must be non-empty and unique")
        peers[node] = addr
    if len(peers) < 1:
        raise ValueError("at least one peer, including this node, is required")
    return peers


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int)
    parser.add_argument("--llm-server", default="127.0.0.1:50055")
    parser.add_argument("--follower", action="store_true")
    parser.add_argument("--leader", default="")
    parser.add_argument("--node-id", default="")
    parser.add_argument("--peers", default="")
    parser.add_argument("--data-dir", default="")
    args = parser.parse_args()
    raft = None
    if args.peers:
        if not args.node_id:
            parser.error("--node-id is required when --peers is used")
        try:
            addrs = parse_peers(args.peers)
        except ValueError as err:
            parser.error(str(err))
        if args.node_id not in addrs:
            parser.error("--peers must include this --node-id")
        own = addrs[args.node_id]
        try:
            peer_port = int(own.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            parser.error("peer addresses must use host:port")
        port = args.port or peer_port
        if port != peer_port:
            parser.error("--port must match this node's address in --peers")
        data_dir = Path(args.data_dir) if args.data_dir else Path("data") / args.node_id
        app = BookingApp()
        raft = RaftNode(
            args.node_id,
            {node: addr for node, addr in addrs.items() if node != args.node_id},
            data_dir / "raft.db",
            app.apply_raft,
        )
        srv, bound = serve(app=app, port=port, llm_url=args.llm_server, bind="[::]", raft=raft)
        print(f"Raft app node {args.node_id} listening on port {bound}")
    else:
        port = args.port or 50051
        srv, bound = serve(
            port=port,
            is_leader=not args.follower,
            leader_url=args.leader,
            llm_url=args.llm_server,
        )
        print(f"ClientService listening on port {bound} (leader={not args.follower})")
    try:
        srv.wait_for_termination()
    finally:
        if raft:
            raft.close()


if __name__ == "__main__":
    main()
