from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class LoginRequest(_message.Message):
    __slots__ = ("username", "password")
    USERNAME_FIELD_NUMBER: _ClassVar[int]
    PASSWORD_FIELD_NUMBER: _ClassVar[int]
    username: str
    password: str
    def __init__(self, username: _Optional[str] = ..., password: _Optional[str] = ...) -> None: ...

class LoginResponse(_message.Message):
    __slots__ = ("status", "token", "message")
    STATUS_FIELD_NUMBER: _ClassVar[int]
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    status: str
    token: str
    message: str
    def __init__(self, status: _Optional[str] = ..., token: _Optional[str] = ..., message: _Optional[str] = ...) -> None: ...

class LogoutRequest(_message.Message):
    __slots__ = ("token",)
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    token: str
    def __init__(self, token: _Optional[str] = ...) -> None: ...

class StatusResponse(_message.Message):
    __slots__ = ("status", "message", "redirect_to", "booking_id")
    STATUS_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    REDIRECT_TO_FIELD_NUMBER: _ClassVar[int]
    BOOKING_ID_FIELD_NUMBER: _ClassVar[int]
    status: str
    message: str
    redirect_to: str
    booking_id: str
    def __init__(self, status: _Optional[str] = ..., message: _Optional[str] = ..., redirect_to: _Optional[str] = ..., booking_id: _Optional[str] = ...) -> None: ...

class PostRequest(_message.Message):
    __slots__ = ("token", "type", "data", "request_id")
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    token: str
    type: str
    data: bytes
    request_id: str
    def __init__(self, token: _Optional[str] = ..., type: _Optional[str] = ..., data: _Optional[bytes] = ..., request_id: _Optional[str] = ...) -> None: ...

class GetRequest(_message.Message):
    __slots__ = ("token", "type", "params")
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    PARAMS_FIELD_NUMBER: _ClassVar[int]
    token: str
    type: str
    params: bytes
    def __init__(self, token: _Optional[str] = ..., type: _Optional[str] = ..., params: _Optional[bytes] = ...) -> None: ...

class Item(_message.Message):
    __slots__ = ("id", "data")
    ID_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    id: str
    data: bytes
    def __init__(self, id: _Optional[str] = ..., data: _Optional[bytes] = ...) -> None: ...

class GetResponse(_message.Message):
    __slots__ = ("status", "items", "message")
    STATUS_FIELD_NUMBER: _ClassVar[int]
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    status: str
    items: _containers.RepeatedCompositeFieldContainer[Item]
    message: str
    def __init__(self, status: _Optional[str] = ..., items: _Optional[_Iterable[_Union[Item, _Mapping]]] = ..., message: _Optional[str] = ...) -> None: ...

class RequestVoteArgs(_message.Message):
    __slots__ = ("to", "term", "last_log_index", "last_log_term")
    FROM_FIELD_NUMBER: _ClassVar[int]
    TO_FIELD_NUMBER: _ClassVar[int]
    TERM_FIELD_NUMBER: _ClassVar[int]
    LAST_LOG_INDEX_FIELD_NUMBER: _ClassVar[int]
    LAST_LOG_TERM_FIELD_NUMBER: _ClassVar[int]
    to: str
    term: int
    last_log_index: int
    last_log_term: int
    def __init__(self, to: _Optional[str] = ..., term: _Optional[int] = ..., last_log_index: _Optional[int] = ..., last_log_term: _Optional[int] = ..., **kwargs) -> None: ...

class RequestVoteReply(_message.Message):
    __slots__ = ("to", "term", "vote_granted")
    FROM_FIELD_NUMBER: _ClassVar[int]
    TO_FIELD_NUMBER: _ClassVar[int]
    TERM_FIELD_NUMBER: _ClassVar[int]
    VOTE_GRANTED_FIELD_NUMBER: _ClassVar[int]
    to: str
    term: int
    vote_granted: bool
    def __init__(self, to: _Optional[str] = ..., term: _Optional[int] = ..., vote_granted: _Optional[bool] = ..., **kwargs) -> None: ...

class LogEntry(_message.Message):
    __slots__ = ("term", "index", "command_type", "payload", "request_id")
    TERM_FIELD_NUMBER: _ClassVar[int]
    INDEX_FIELD_NUMBER: _ClassVar[int]
    COMMAND_TYPE_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    term: int
    index: int
    command_type: str
    payload: bytes
    request_id: str
    def __init__(self, term: _Optional[int] = ..., index: _Optional[int] = ..., command_type: _Optional[str] = ..., payload: _Optional[bytes] = ..., request_id: _Optional[str] = ...) -> None: ...

class AppendEntriesArgs(_message.Message):
    __slots__ = ("to", "term", "prev_index", "prev_term", "commit_index", "entries")
    FROM_FIELD_NUMBER: _ClassVar[int]
    TO_FIELD_NUMBER: _ClassVar[int]
    TERM_FIELD_NUMBER: _ClassVar[int]
    PREV_INDEX_FIELD_NUMBER: _ClassVar[int]
    PREV_TERM_FIELD_NUMBER: _ClassVar[int]
    COMMIT_INDEX_FIELD_NUMBER: _ClassVar[int]
    ENTRIES_FIELD_NUMBER: _ClassVar[int]
    to: str
    term: int
    prev_index: int
    prev_term: int
    commit_index: int
    entries: _containers.RepeatedCompositeFieldContainer[LogEntry]
    def __init__(self, to: _Optional[str] = ..., term: _Optional[int] = ..., prev_index: _Optional[int] = ..., prev_term: _Optional[int] = ..., commit_index: _Optional[int] = ..., entries: _Optional[_Iterable[_Union[LogEntry, _Mapping]]] = ..., **kwargs) -> None: ...

class AppendEntriesReply(_message.Message):
    __slots__ = ("to", "term", "entry_appended", "match_index")
    FROM_FIELD_NUMBER: _ClassVar[int]
    TO_FIELD_NUMBER: _ClassVar[int]
    TERM_FIELD_NUMBER: _ClassVar[int]
    ENTRY_APPENDED_FIELD_NUMBER: _ClassVar[int]
    MATCH_INDEX_FIELD_NUMBER: _ClassVar[int]
    to: str
    term: int
    entry_appended: bool
    match_index: int
    def __init__(self, to: _Optional[str] = ..., term: _Optional[int] = ..., entry_appended: _Optional[bool] = ..., match_index: _Optional[int] = ..., **kwargs) -> None: ...

class LLMRequest(_message.Message):
    __slots__ = ("request_id", "query", "context")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    QUERY_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    query: str
    context: str
    def __init__(self, request_id: _Optional[str] = ..., query: _Optional[str] = ..., context: _Optional[str] = ...) -> None: ...

class LLMResponse(_message.Message):
    __slots__ = ("request_id", "answer")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    ANSWER_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    answer: str
    def __init__(self, request_id: _Optional[str] = ..., answer: _Optional[str] = ...) -> None: ...
