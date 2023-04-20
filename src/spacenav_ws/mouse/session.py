"""Framework for linking the navigation controller to the WAMP events."""

import asyncio
import logging
from typing import Any, Coroutine

from spacenav_ws import event
from spacenav_ws import wamp
from spacenav_ws.mouse import controller
from spacenav_ws.mouse import mouse3d


def rpc_uri(name: str) -> str:
  return f'wss://127.51.68.120/3dconnexion#{name}'


def resource_uri(*parts: list[str]) -> str:
  name = '/'.join(parts)
  return f'wss://127.51.68.120/3dconnexion{name}'


class MouseSession:

  def __init__(self, session: wamp.WampSession):
    self._com = session

    self._mouse = None
    self._controller = None

    self._rpcs = {}
    self._ws_reads = []
    self._mouse_reads = []
    self._pending_handlers = []

    session.on(rpc_uri('create'), self.create)
    session.on(rpc_uri('update'), self.client_update)

    self._com.on(wamp.WAMP_MSG_TYPE.SUBSCRIBE, self.subscription)
    self._com.on(wamp.WAMP_MSG_TYPE.CALLRESULT, self.rpc_finished)
    self._com.on(wamp.WAMP_MSG_TYPE.CALLERROR, self.rpc_error)

  @property
  def controller_uri(self) -> str:
    return resource_uri(f'3dcontroller/{self._controller.name}')

  async def create(self, resource_uri: str, *args: list[Any]):
    resource_uri = self._com.resolve(resource_uri)
    logging.info(f'Creating {resource_uri}')

    obj = resource_uri.split(':')[1]
    if obj.endswith('3dmouse'):
      return self.create_mouse(*args)
    elif obj.endswith('3dcontroller'):
      return await self.create_controller(args[0], **args[1])

  def create_mouse(self, version: str) -> dict[str:str]:
    self._mouse = mouse3d.Mouse3d()
    logging.info(f'"Creating" 3d mouse "{self._mouse.name}" '
                 f'for client verssion {version}')
    return {'connexion': self._mouse.name}

  async def create_controller(self, mouse_id: str, version: int,
                              name: str) -> dict[str:str]:
    # TODO(blakely): Reorganize this to break circular dependency.
    ctrl = controller.Controller(self._mouse, self)
    logging.info(f'Created controller "{ctrl.name}" '
                 f'for mouse "{mouse_id}", for client {name}, '
                 f'version {version}')

    await ctrl.connect()

    self._controller = ctrl
    self._expect_mouse()
    return {'instance': self._controller.name}

  async def subscription(self, resource_uri: str):
    logging.info(f'Registering subscription to: {resource_uri}')
    await self._controller.reset()

  async def write_client(self, property: str, value: Any):
    event = wamp.Event(self.controller_uri,
                       wamp.Call.new('self:update', '', property, value))

  def _expect_message(self, name):
    self._ws_reads.append(
        asyncio.create_task(self._com.process_message(), name=name))

  def _expect_mouse(self):
    self._mouse_reads.append(
        asyncio.create_task(self._controller.update(), name='Mouse'))

  async def begin(self):
    await self._com.begin()
    self._expect_message('begin')

  @property
  def reads(self):
    return self._ws_reads + self._mouse_reads + self._pending_handlers

  def log(self, msg=None):
    if msg is None:
      msg = ''
    logging.info(f'Reads {msg}: {len(self.reads)} ('
                 f'{len(self._ws_reads)}/'
                 f'{len(self._mouse_reads)}/'
                 f'{len(self._pending_handlers)})')

  async def process(self):
    # TODO(blakely): Error handling. Currently they're pretty much just dropped
    # on the ground... :/
    done, _ = await asyncio.wait(
        self.reads, timeout=1, return_when='FIRST_COMPLETED')
    for done_task in done:
      if done_task in self._mouse_reads:
        motion = done_task.result()
        logging.debug(f'Got mouse update: {motion}')
        if self._controller:
          if isinstance(motion, event.MotionEvent):
            update_coroutine = self._controller.motion(motion)
            if update_coroutine:
              self._pending_handlers.append(
                  asyncio.create_task(update_coroutine, name='motion'))
        self._mouse_reads.remove(done_task)
        self._expect_mouse()
        continue
      pending_call = done_task.result()
      if isinstance(pending_call, Coroutine):
        self._pending_handlers.append(asyncio.create_task(pending_call,))
      if done_task in self._pending_handlers:
        self._pending_handlers.remove(done_task)
      elif done_task in self._ws_reads:
        self._expect_message('unified EM')
        self._ws_reads.remove(done_task)

  async def shutdown(self):
    for t in self.reads:
      t.cancel()

  async def client_update(self, controller_id: str, args: dict[str, Any]):
    logging.info(f'Got update for {controller_id}: {args}')

  async def rpc_finished(self, call_id: str, *args):
    rpc = self._rpcs.get(call_id, None)
    if rpc is None:
      logging.error('Got unexpected result for unknown RPC id %s: %s', call_id,
                    args)
      return
    rpc['result'] = args
    rpc['gate'].set()

  async def rpc_error(self, call_id: str, error_uri: str, error_desc: str):
    rpc = self._rpcs.get(call_id, None)
    if rpc is None:
      logging.error('Got unexpected error for unknown RPC id %s: %s -> %s',
                    call_id, error_uri, error_desc)
      return
    rpc['error'] = (error_uri, error_desc)
    rpc['gate'].set()

  async def write(self, *args):
    return await self._client_rpc('self:update', *args)

  async def read(self, *args):
    return await self._client_rpc('self:read', *args)

  async def _client_rpc(self, method: str, *args):
    # Set up the call
    call = wamp.Call.new(method, '', *args)
    # Launch RPC in background as task.
    await self._com.send_event(
        wamp.Event(self.controller_uri, call.serialize_with_id()))

    gate = asyncio.Event()
    rpc = {
        'gate': gate,
        'result': None,
        'error': None,
    }
    self._rpcs[call.call_id] = rpc

    await gate.wait()

    if rpc['error'] is not None:
      # TODO(blakely): Should be something else other than valueerror
      raise ValueError(rpc['error'])

    return rpc['result']
