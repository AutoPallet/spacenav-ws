"""Basic WAMP V1 protocol."""

import abc
import dataclasses
import enum
import json
import logging
import random
import string
from typing import Any, Union

import fastapi


class WAMP_MSG_TYPE(enum.IntEnum):
  WELCOME = 0
  PREFIX = 1
  CALL = 2
  CALLRESULT = 3
  CALLERROR = 4
  SUBSCRIBE = 5
  UNSUBSCRIBE = 6
  PUBLISH = 7
  EVENT = 8


class WampMessage(abc.ABC):
  ID: WAMP_MSG_TYPE

  def serialize(self) -> list[Any]:
    pass

  def serialize_with_id(self) -> list[Any]:
    return [self.ID] + self.serialize()


@dataclasses.dataclass
class Welcome(WampMessage):
  ID = WAMP_MSG_TYPE.WELCOME
  session_id: str
  version: int
  server_ident: str

  def serialize(self):
    return [self.session_id, self.version, self.server_ident]


@dataclasses.dataclass
class Prefix(WampMessage):
  ID = WAMP_MSG_TYPE.PREFIX

  prefix: str
  uri: str

  def serialize(self):
    return [self.prefix, self.uri]


@dataclasses.dataclass
class Call(WampMessage):
  ID = WAMP_MSG_TYPE.CALL

  call_id: str
  proc_uri: str
  args: list[Any]

  def __init__(self, call_id, proc_uri: str, *args, **kwargs):
    self.call_id = call_id
    self.proc_uri = proc_uri
    self.args = args

  def serialize(self):
    return [self.call_id, self.proc_uri, *self.args]

  @classmethod
  def new(cls, proc_uri: str, *args, call_id_len=18) -> 'Call':
    return Call(_rand_id(call_id_len), proc_uri, *args)


@dataclasses.dataclass
class CallResult(WampMessage):
  ID = WAMP_MSG_TYPE.CALLRESULT

  call_id: str
  result: Any

  def serialize(self):
    return [self.call_id, self.result]


@dataclasses.dataclass
class CallError(WampMessage):
  ID = WAMP_MSG_TYPE.CALLERROR

  call_id: str
  error_uri: str
  error_desc: str

  def serialize(self):
    return [self.call_id, self.error_uri, self.error_desc]


@dataclasses.dataclass
class Subscribe(WampMessage):
  ID = WAMP_MSG_TYPE.SUBSCRIBE

  topic_uri: str

  def serialize(self):
    return [self.topic_uri]


@dataclasses.dataclass
class Event(WampMessage):
  ID = WAMP_MSG_TYPE.EVENT

  topic_uri: str
  args: Any

  def __init__(self, topic_uri: str, *args):
    self.topic_uri = topic_uri
    self.args = args

  def serialize(self):
    return [self.topic_uri, *self.args]


WAMP_TYPES = {
    WAMP_MSG_TYPE.WELCOME: Welcome,
    WAMP_MSG_TYPE.PREFIX: Prefix,
    WAMP_MSG_TYPE.CALL: Call,
    WAMP_MSG_TYPE.CALLRESULT: CallResult,
    WAMP_MSG_TYPE.CALLERROR: CallError,
    WAMP_MSG_TYPE.SUBSCRIBE: Subscribe,
    WAMP_MSG_TYPE.EVENT: Event,
}


def _rand_id(len) -> str:
  return ''.join(random.choices(string.ascii_uppercase + string.digits, k=len))


class WampSession:

  def __init__(self, websocket: fastapi.WebSocket, id_length=16):
    self._socket = websocket
    self._server_id = 'spacenav-ws v0.0.1'
    self._session_id = _rand_id(id_length)

    self._prefixes = {}

    self._msg_handlers = {
        WAMP_MSG_TYPE.PREFIX: self.add_prefix,
        WAMP_MSG_TYPE.CALL: self.call,
        WAMP_MSG_TYPE.CALLRESULT: self.callresult,
        WAMP_MSG_TYPE.CALLERROR: self.callerror,
        WAMP_MSG_TYPE.SUBSCRIBE: self.subscribe,
    }

    self._call_handlers = {}

  async def begin(self):
    await self._socket.accept(subprotocol="wamp")
    await self.send_message(Welcome(self._session_id, 1, self._server_id))

  def parse_message(self, msg: Union[str, list[Union[int,
                                                     Any]]]) -> WampMessage:
    if isinstance(msg, str):
      msg: list[Union[int, Any]] = json.loads(msg)

    msg_id, *args = msg
    ctor = WAMP_TYPES.get(msg_id, None)
    if ctor is None:
      raise ValueError(f'Unknown message type: {msg_id}')
    return ctor(*args)

  def resolve(self, uri: str) -> str:
    if ':' not in uri:
      return uri
    prefix, resource = uri.split(':')
    return ''.join([self._prefixes[prefix], resource])

  async def send_message(self, message: WampMessage):
    await self._socket.send_json(message.serialize_with_id())

  async def process_message(self):
    msg = self.parse_message(await self._socket.receive_json())
    handler = self._msg_handlers.get(msg.ID, None)
    if handler is None:
      logging.warn('Unhandled message type: %s', msg.ID)
      return
    return handler(msg)

  async def add_prefix(self, msg: Prefix):
    self._prefixes[msg.prefix] = msg.uri

  def on(self, call: WAMP_MSG_TYPE, handler: Any):
    self._call_handlers[call] = handler

  async def call(self, msg: Call):
    rpc_name = self.resolve(msg.proc_uri)

    rpc = self._call_handlers.get(rpc_name, None)

    if rpc is None:
      logging.warn('Unhandled RPC: %s (received as %s)', rpc_name, msg.proc_uri)
      await self.send_message(
          CallError(msg.call_id, 'Err', f'Unhandled RPC: {msg.proc_uri}'))
      return

    result = await rpc(*msg.args)
    await self.send_message(CallResult(msg.call_id, result))

  async def subscribe(self, msg: Subscribe):
    resource = self.resolve(msg.topic_uri)
    handler = self._subscribables.get(resource, None)
    if handler is None:
      logging.warn('Unknown subscribable: %s', resource)
      return
    await handler(resource)

  async def send_event(self, msg: Event, expect_reply=True):
    await self.send_message(msg)

  async def callresult(self, msg: CallResult):
    handler = self._call_handlers.get(WAMP_MSG_TYPE.CALLRESULT, None)
    if handler is None:
      logging.warn('No callresult handler for msg: %s', msg)
      return
    args = msg.result
    if msg.result is None:
      args = [None]

    try:
      await handler(msg.call_id, *args)
    except TypeError:
      # Caused when the response is not multiple args e.g. a bool. Hooray for
      # consistency in APIs! :C
      await handler(msg.call_id, args)

  async def callerror(self, msg: CallError):
    handler = self._call_handlers.get(WAMP_MSG_TYPE.CALLRESULT, None)
    if handler is None:
      logging.warn('No callerror handler for msg: %s', msg)
      return

    await handler(msg.call_id, msg.error_uri, msg.error_desc)

  async def subscribe(self, msg: Subscribe):
    handler = self._call_handlers.get(WAMP_MSG_TYPE.SUBSCRIBE, None)
    if handler is None:
      logging.warn('No subscribe handler for msg: %s', msg)
      return
    await handler(msg.topic_uri)
