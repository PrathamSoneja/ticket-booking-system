import argparse
import json
import logging
import os
from concurrent import futures
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_urlsafe
from threading import Lock
from uuid import uuid4

import grpc

from ticket_booking.application import BookingApp
from ticket_booking.faq_data import FaqIndex, in_domain
from ticket_booking.generated import ticket_booking_pb2 as pb
from ticket_booking.generated import ticket_booking_pb2_grpc as pb_grpc
from ticket_booking.raft_node import RaftNode
from ticket_booking.raft_service import RaftService

log = logging.getLogger("ticket_booking")

FORWARDED_HEADER = "x-tb-forwarded"


def _json_object(raw):
    if not raw:
        return {}, ""
    try:
        data = json.loads(raw.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "Request payload is not valid JSON."
    if not isinstance(data, dict):
        return None, "Request payload must be a JSON object."
    return data, ""


def _is_forwarded(ctx):
    if ctx is None:
        return False
    return any(key == FORWARDED_HEADER for key, _ in ctx.invocation_metadata() or ())


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
        self.read_timeout = 2.0
        self.llm_timeout = float(os.environ.get("TB_LLM_TIMEOUT", "60"))
        self.faq = FaqIndex()
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
                return self._forward("Login", req)
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
                return self._forward("Signup", req)
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
                    return pb.LoginResponse(status=status, message="Signup could not reach a Raft majority.")
                return pb.LoginResponse(status="OK", token=tok, message="Account registered.")
        out = self.app.signup(req.username, req.password)
        return pb.LoginResponse(status=out.status, token=out.token, message=out.message)

    def Logout(self, req, ctx):
        if self.raft:
            if self.raft.role != "leader":
                return self._forward("Logout", req)
            if self.app.auth.token_user(req.token) is None:
                return pb.StatusResponse(status="AUTH_FAILED", message="Invalid or expired session.")
            key = self.app.auth.token_key(req.token)
            body = json.dumps({"token_hash": key}, separators=(",", ":")).encode()
            status, _ = self.raft.propose("AUTH_LOGOUT", body, "logout:" + key)
            if status != "OK":
                return pb.StatusResponse(status=status, message="Logout could not reach a Raft majority.")
            return pb.StatusResponse(status="OK", message="Logged out.")
        out = self.app.logout(req.token)
        return pb.StatusResponse(status=out.status, message=out.message)

    def Get(self, req, ctx):
        data, err = _json_object(req.params)
        if err:
            return pb.GetResponse(status="INVALID_REQUEST", message=err)
        params = {}
        for k in data:
            params[str(k)] = str(data[k])
        if self.raft and req.type != "CLUSTER_STATUS":
            if self.raft.role != "leader":
                if _is_forwarded(ctx):
                    return pb.GetResponse(status="NO_LEADER", message="Forwarded read reached a non-leader.")
                wait = self.llm_timeout + 5.0 if req.type == "FAQ" else 3.0
                return self._forward("Get", req, wait)
            barrier = self.raft.read_barrier(self.read_timeout)
            if barrier != "OK":
                if barrier == "NOT_LEADER":
                    barrier = "NO_LEADER"
                return pb.GetResponse(status=barrier, message="Read could not be confirmed by a Raft majority.")
        if req.type == "CLUSTER_STATUS":
            if self.app.auth.token_user(req.token) is None:
                return pb.GetResponse(status="AUTH_FAILED", message="A valid session token is required.")
            if self.raft:
                status = self.raft.status()
                status["log_digest"] = self.raft.log_digest(status["commit_index"])
                status["state_digest"] = self.app.state_digest()
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
            return self._faq(params)
        proto_items = []
        for it in items:
            key = it.get("booking_id") or it.get("show_id") or it.get("seat_id") or ""
            proto_items.append(pb.Item(id=key, data=json.dumps(it).encode()))
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
        data, err = _json_object(req.data)
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
            if out.status == "NOT_LEADER":
                return pb.StatusResponse(
                    status="NOT_LEADER",
                    message=out.message,
                    redirect_to=self.raft.leader_address or "",
                )
        else:
            out = self.app.post(req.token, req.type, clean, req.request_id)
        message = out.message
        if out.raft_log_index:
            message = json.dumps(
                {
                    "_booking_receipt_v1": True,
                    "message": out.message,
                    "receipt": {
                        "leader": out.raft_leader,
                        "term": out.raft_term,
                        "log_index": out.raft_log_index,
                        "commit_index": out.raft_commit_index,
                        "last_applied": out.raft_last_applied,
                        "committed": out.raft_committed,
                        "replayed": out.request_replayed,
                    },
                },
                separators=(",", ":"),
            )
        return pb.StatusResponse(status=out.status, message=message, booking_id=out.booking_id)

    def _faq(self, params):
        q = params.get("query", "").strip()
        if not q:
            return pb.GetResponse(status="INVALID_REQUEST", message="FAQ requests require a query parameter.")
        cid = "faq-" + uuid4().hex  # correlation ID; never the session token
        faq_txt, faq_ids = self.faq.retrieve(q)
        live = self.app.faq_context(q, {k: v for k, v in params.items() if k != "context"})
        support = {
            "correlation_id": cid,
            "sources": faq_ids,
            "faq_context": faq_txt,
            "live_context": live,
            "generated": False,
            "fallback": False,
        }

        def reply(status, message):
            item = pb.Item(id="faq_support", data=json.dumps(support).encode())
            return pb.GetResponse(status=status, message=message, items=[item])

        if not in_domain(q) or (not faq_ids and not live):
            return reply(
                "OUT_OF_DOMAIN",
                "I can only answer questions about shows, seats, bookings, payments, " "cancellations and refunds.",
            )
        ans = None
        if self.llm is not None:
            try:
                ctx_str = self.app.faq_context(q, params)
                ans = self.llm.GetLLMAnswer(
                    pb.LLMRequest(request_id=cid, query=q, context=ctx_str), timeout=self.llm_timeout
                ).answer
            except grpc.RpcError as err:
                log.warning("LLM RPC failed (correlation_id=%s): %s", cid, err.code())
        if ans:
            support["generated"] = True
            return reply("OK", ans)
        support["fallback"] = True
        base = "The FAQ service is not configured." if self.llm is None else "The FAQ service could not be reached."
        text = faq_txt or live
        return reply(
            "LLM_UNAVAILABLE",
            base + " Fallback - matching FAQ text shown verbatim, not AI-generated: " + text,
        )

    def _forward(self, method, req, timeout=3.0):
        if method == "Logout":
            response = pb.StatusResponse
        elif method == "Get":
            response = pb.GetResponse
        else:
            response = pb.LoginResponse
        leader = self.raft.leader_rpc_address or ""
        if not leader:
            self.raft.wait_for_leader(1.0)
            leader = self.raft.leader_rpc_address or ""
        if not leader:
            return response(status="NO_LEADER", message="The cluster has not elected a leader.")
        ch = grpc.insecure_channel(leader)
        stub = pb_grpc.ClientServiceStub(ch)
        try:
            call = getattr(stub, method)
            res = call(req, timeout=timeout, metadata=((FORWARDED_HEADER, "1"),))
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
    pb_grpc.add_ClientServiceServicer_to_server(AppServer(app, pay, is_leader, leader_url, llm_url, raft), srv)
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
    parser.add_argument("--client-addresses", default="")
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
        try:
            client_addrs = parse_peers(args.client_addresses) if args.client_addresses else addrs
        except ValueError as err:
            parser.error(str(err))
        if client_addrs.keys() != addrs.keys():
            parser.error("--client-addresses must include exactly the node IDs in --peers")
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
            client_peers={node: addr for node, addr in client_addrs.items() if node != args.node_id},
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
