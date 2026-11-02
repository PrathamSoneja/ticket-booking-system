from .generated import ticket_booking_pb2_grpc as pb_grpc


class RaftService(pb_grpc.RaftServiceServicer):
    def __init__(self, node):
        self.node = node

    def RequestVote(self, req, ctx):
        return self.node.request_vote(req)

    def AppendEntries(self, req, ctx):
        return self.node.append_entries(req)
