import asyncio
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
    return f'3dcontroller/{self.name}'

  async def update(self):
    mouse_event = await self._reader.read(32)
    message = event.from_message(struct.unpack("iiiiiiii", mouse_event))
    return message


class MouseSession:

  def __init__(self, session: wamp.WampSession):
    self._com = session

    self._mouse = None
    self._controller = None

    session.on(_rpc('create'), self.create)

  async def create(self, resource_uri: str, *args: list[Any]):
    resource_uri = self._com.resolve(resource_uri)
    print(f'Creating {resource_uri}', flush=True)

    obj = resource_uri.split(':')[1]
    if obj.endswith('3dmouse'):
      return self.create_mouse(*args)
    elif obj.endswith('3dcontroller'):
      return await self.create_controller(args[0], **args[1])

  def create_mouse(self, version: str) -> dict[str:str]:
    self._mouse = Mouse3d()
    print(
        f'"Creating" 3d mouse "{self._mouse.name}" '
        f'for client verssion {version}',
        flush=True)
    return {'connexion': self._mouse.name}

  async def create_controller(self, mouse_id: str, version: int,
                              name: str) -> dict[str:str]:
    controller = Controller(self._mouse)
    print(
        f'Created controller "{controller.name}" '
        f'for mouse "{mouse_id}", for client {name}, '
        f'version {version}',
        flush=True)

    print(
        'Connected. Attempting to connect to mouse at '
        f'{controller.socket_path}...',
        flush=True)
    await controller.connect()

    self._com.add_subscribable(_resource(controller.uri), self.on_subscription)

    self._controller = controller
    return {'instance': self._controller.name}

  async def on_subscription(self, resource_uri: str):
    print(f'Registering subscription to: {resource_uri}', flush=True)

  async def process(self):
    await self._com.begin()

    client_message = asyncio.create_task(self._com.process_message())
    mouse_event = None

    while True:
      if mouse_event is None and self._controller is not None:
        mouse_event = asyncio.create_task(self._controller.update())
      done, _ = await asyncio.wait(
          [x for x in [client_message, mouse_event] if x is not None],
          timeout=1,
          return_when='FIRST_COMPLETED')
      if client_message in done:
        # Retrieve result
        client_message.result()
        client_message = asyncio.create_task(self._com.process_message())
      if mouse_event in done:
        print(f'Got mouse update: {mouse_event.result()}', flush=True)
        mouse_event = asyncio.create_task(self._controller.update())
