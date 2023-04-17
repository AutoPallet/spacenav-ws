import asyncio
import logging
import struct
from typing import Any

from spacenav_ws import event
from spacenav_ws import wamp


def _rpc(name: str) -> str:
  return f'wss://127.51.68.120/3dconnexion#{name}'


def _resource(*parts: list[str]) -> str:
  name = '/'.join(parts)
  return f'wss://127.51.68.120/3dconnexion{name}'


class Mouse3d:

  def __init__(self):
    self.name = 'mouse0'


class Controller:

  def __init__(self, mouse: Mouse3d, spnav_socket='/var/run/spnav.sock'):
    self.name = 'controller0'
    self.mouse = mouse
    self.socket_path = spnav_socket
    self._reader = None

  async def connect(self):
    self._reader, __ = await asyncio.open_unix_connection(self.socket_path)

  @property
  def uri(self) -> str:
    return _resource(f'3dcontroller/{self.name}')

  async def update(self):
    mouse_event = await self._reader.read(32)
    message = event.from_message(struct.unpack("iiiiiiii", mouse_event))
    return message


class MouseSession:

  def __init__(self, session: wamp.WampSession):
    self._com = session

    self._mouse = None
    self._controller = None

    self._rpcs = {}
    self._ws_reads = []
    self._mouse_reads = []
    self._rpc_reads = []

    session.on(_rpc('create'), self.create)
    session.on(_rpc('update'), self.client_update)

    self._com.on(wamp.WAMP_MSG_TYPE.SUBSCRIBE, self.subscription)
    self._com.on(wamp.WAMP_MSG_TYPE.CALLRESULT, self.rpc_finished)

  async def create(self, resource_uri: str, *args: list[Any]):
    resource_uri = self._com.resolve(resource_uri)
    logging.info(f'Creating {resource_uri}')

    obj = resource_uri.split(':')[1]
    if obj.endswith('3dmouse'):
      return self.create_mouse(*args)
    elif obj.endswith('3dcontroller'):
      return await self.create_controller(args[0], **args[1])

  def create_mouse(self, version: str) -> dict[str:str]:
    self._mouse = Mouse3d()
    logging.info(f'"Creating" 3d mouse "{self._mouse.name}" '
                 f'for client verssion {version}')
    return {'connexion': self._mouse.name}

  async def create_controller(self, mouse_id: str, version: int,
                              name: str) -> dict[str:str]:
    controller = Controller(self._mouse)
    logging.info(f'Created controller "{controller.name}" '
                 f'for mouse "{mouse_id}", for client {name}, '
                 f'version {version}')

    logging.info('Connected. Attempting to connect to mouse at '
                 f'{controller.socket_path}...')
    await controller.connect()

    self._controller = controller
    self._expect_mouse()
    return {'instance': self._controller.name}

  async def subscription(self, resource_uri: str):
    logging.info(f'Registering subscription to: {resource_uri}')
    # TODO(blakely): This may not be the right place for this...

    await self.update_client('hit.selectionOnly', False)

  async def update_client(self, property: str, value: Any):
    event = wamp.Event(self._controller.uri,
                       wamp.Call.new('self:update', '', property, value))

  def _expect_message(self):
    self._ws_reads.append(asyncio.create_task(self._com.process_message()))

  def _expect_rpc(self):
    self._rpc_reads.append(asyncio.create_task(self._com.process_message()))

  def _expect_mouse(self):
    self._mouse_reads.append(asyncio.create_task(self._controller.update()))

  async def process(self):
    await self._com.begin()

    self._expect_message()

    while True:
      if not self._ws_reads:
        self._expect_message()

      done, _ = await asyncio.wait(
          self._ws_reads + self._mouse_reads + self._rpc_reads,
          timeout=1,
          return_when='FIRST_COMPLETED')
      for done_task in done:
        result = done_task.result()
        if done_task in self._mouse_reads:
          motion = result
          logging.info(f'Got mouse update: {motion}')
          self._mouse_reads.remove(done_task)
          self._expect_mouse()
        elif done_task in self._rpc_reads:
          self._rpc_reads.remove(done_task)
        else:
          self._ws_reads.remove(done_task)
          self._expect_message()

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

  async def update_client(self, *args):
    # Set up the call
    call = wamp.Call.new('self:update', '', *args)
    # Launch RPC in background as task.
    await self._com.send_event(
        wamp.Event(self._controller.uri, call.serialize_with_id()))

    gate = asyncio.Event()
    rpc = {
        'gate': gate,
        'result': None,
        'error': None,
    }
    self._rpcs[call.call_id] = rpc

    self._expect_rpc()
    await gate.wait()

    if rpc['error'] is not None:
      # TODO(blakely): Should be something else other than valueerror
      raise ValueError(rpc['error'])

    return rpc['result']
