import asyncio
from asyncio import streams
import enum
import json
from pathlib import Path
import random
import socket
import string
from struct import unpack

from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
import numpy as np
import uvicorn

from spacenav_ws.event import from_message, MotionEvent

import logging

# logging.basicConfig(level=logging.DEBUG)

BASE_DIR = Path(__file__).resolve().parent

origins = [
    "https://127.51.68.120",
    "https://127.51.68.120:8181",
    "https://3dconnexion.com",
    "https://cad.onshape.com",
]

app = FastAPI()
templates = Jinja2Templates(directory=BASE_DIR / "templates")

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


WampWelcome = list[WAMP_MSG_TYPE, str, int, str]


def wamp_welcome() -> list[WAMP_MSG_TYPE, str, int, str]:
  return [WAMP_MSG_TYPE.WELCOME, 'asdf', 1, 'spacenav-ws']


def wamp_serialize(msg: list[WAMP_MSG_TYPE, str, int, str]) -> str:
  return [int(msg[0])] + msg[1:]


@app.get("/test")
async def get(request: Request):
  return templates.TemplateResponse("test_ws.html", {"request": request})


@app.route("/3dconnexion/nlproxy")
async def nlproxy(request):
  return JSONResponse({"port": "8181"})


class WampProto:

  def __init__(self, socket: WebSocket, stream: asyncio.streams.StreamReader):
    self._socket = socket
    self._data_stream = stream

    self._handlers = {
        # WAMP_MSG_TYPE.WELCOME: self.welcome,
        WAMP_MSG_TYPE.PREFIX:
            self.prefix,
        WAMP_MSG_TYPE.CALL:
            self.call,
        WAMP_MSG_TYPE.SUBSCRIBE:
            self.subscribe,
    }

    self._prefixes = {}
    self._session = None
    self._calls = {}
    self._controller = None

  async def start(self):
    await self._socket.accept(subprotocol="wamp")

    await self._socket.send_json(self.welcome())

  def _new_session(self) -> str:
    sess = ''.join(random.choices(string.ascii_uppercase + string.digits, k=32))
    self._session = sess
    return sess

  def welcome(self) -> WampWelcome:
    return [WAMP_MSG_TYPE.WELCOME, self._new_session(), 1, 'spacenav-ws']

  async def prefix(self, alias: str, replace: str):
    self._prefixes[replace] = alias

  async def listen(self):
    print('Listening...', flush=True)
    msg = await asyncio.wait_for(self._socket.receive_json(), timeout=1.0)
    print(f'Received: {msg}', flush=True)
    if msg[0] in self._handlers:
      print(f'Handling {WAMP_MSG_TYPE(msg[0])}')
      await self._handlers[msg[0]](*msg[1:])
    else:
      print(f'UNKNOWN {WAMP_MSG_TYPE(msg[0])}')

  def call_result(self, call_id, *results):
    self._calls[call_id]['result'] = results
    return [WAMP_MSG_TYPE.CALLRESULT, call_id, *results]

  async def call(self, call_id: str, method: str, *args: list[str]):
    print(f'Attempting to call {method} on {call_id}')
    self._calls[call_id] = {
        'method': method,
        'result': None,
        'error': None,
    }

    res = None
    if '3dmouse' in args[0]:
      print(f'Creating mouse to {method} on {call_id}')
      res = self.call_result(call_id, {'connexion': 'mouse0'})
    elif '3dcontroller' in args[0]:
      print(f'Creating controller to {method} on {call_id}')
      self._controller = 'controller0'
      res = self.call_result(call_id, {'instance': self._controller})

    if res is not None:
      print(f'Sending result: {res}')
      await self._socket.send_json(res)
    print('Done')

  async def subscribe(self, target: str):
    print(f'Subscribed to {target}', flush=True)

    if self._controller:
      print(
          'toot',
          await self._socket.send_json([
              WAMP_MSG_TYPE.EVENT, target,
              [WAMP_MSG_TYPE.CALL, 'WAT', 'self:read', 'WAT2', 'view.affine']
          ]),
          flush=True)
      print('Waiting for read...', flush=True)
      res = await asyncio.wait_for(self._socket.receive_json(), timeout=1.0)
      print(f'Got: {res}', flush=True)
    while True:
      chunk = await self._data_stream.read(32)
      message = from_message(unpack("iiiiiiii", chunk))
      print(message, flush=True)
      assert isinstance(message, MotionEvent)

      # fnUpdate - motion

      await self._socket.send_json([
          WAMP_MSG_TYPE.EVENT, target,
          [2, 'WAT', 'self:update', 'WAT2', 'motion',
           message.to_3dconn()]
      ])


@app.websocket("/")
@app.websocket("/3dconnexion")
async def websocket_endpoint(websocket: WebSocket):
  reader, __ = await asyncio.open_unix_connection("/var/run/spnav.sock")

  print('Accepting', flush=True)
  wamp = WampProto(websocket, reader)
  await wamp.start()

  print('Sent', flush=True)
  while True:
    await wamp.listen()
    # print(f'Data: {data}', flush=True)
    # chunk = await reader.read(32)
    # message = from_message(unpack("iiiiiiii", chunk))
    # print(message)
    # await websocket.send_json(message.to_3dconn())


if __name__ == "__main__":
  uvicorn.run(
      "spacenav_ws.main:app",
      host="0.0.0.0",
      port=8000,
      reload=True,
      log_level="debug",
  )
