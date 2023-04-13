from typing import Any

from spacenav_ws import wamp


def _rpc(name: str) -> str:
  return f'wss://127.51.68.120/3dconnexion#{name}'


def _resource(name: str) -> str:
  return f'wss://127.51.68.120/3dconnexion{name}'


class Mouse:

  def __init__(self, session: wamp.WampSession):
    self._com = session

    self._mouse_name = None
    self._controller_name = None

    session.on(_rpc('create'), self.create)

  async def create(self, resource_uri: str, *args: list[Any]):
    resource_uri = self._com.resolve(resource_uri)
    print(f'Creating {resource_uri}', flush=True)

    obj = resource_uri.split(':')[1]
    if obj.endswith('3dmouse'):
      return self.create_mouse(*args)
    elif obj.endswith('3dcontroller'):
      return self.create_controller(args[0], **args[1])

  def create_mouse(self, version: str) -> dict[str:str]:
    self._mouse_name = 'mouse0'
    print(
        f'"Creating" 3d mouse "{self._mouse_name}" '
        f'for client verssion {version}',
        flush=True)
    return {'connexion': self._mouse_name}

  def create_controller(self, mouse_id: str, version: int,
                        name: str) -> dict[str:str]:
    self._controller_name = 'controller0'
    print(
        f'"Creating" controller "{self._controller_name}" '
        f'for mosue "{mouse_id}", for client {name}, '
        f'version {version}',
        flush=True)
    return {'instance': self._controller_name}
