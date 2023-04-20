"""Machinery to hold navigation state for SpaceMouse."""

import asyncio
import dataclasses
import logging
import struct

import numpy as np
from scipy.spatial import transform
from spacenav_ws import event
from spacenav_ws.mouse import mouse3d
# TODO(blakely): Break circular dependency for stronger typing.
# from spacenav_ws.mouse import session


class Controller:

  @dataclasses.dataclass
  class PredefinedViews:
    front: np.ndarray

  @dataclasses.dataclass
  class World:
    coordinate_frame: np.ndarray

  @dataclasses.dataclass
  class Camera:
    affine: np.ndarray = dataclasses.field()
    constructionPlane: np.ndarray
    extents: np.ndarray
    # Apparently causes the 3dconnexion demo app to crash...?
    # fov: np.ndarray
    frustum: np.ndarray
    perspective: bool
    target: np.ndarray
    rotatable: np.ndarray

    def __post_init__(self):
      self.affine = np.asarray(self.affine).reshape([4, 4])

  def __init__(self,
               mouse: mouse3d.Mouse3d,
               session: 'MosueSession',
               spnav_socket='/var/run/spnav.sock'):
    self.name = 'controller0'
    self.mouse = mouse
    self._socket_path = spnav_socket
    self._reader = None
    self._session = session

    self.affine = None
    self.coordinate_system = None

  async def connect(self):
    logging.info('Connected. Attempting to connect to mouse at '
                 f'{self._socket_path}...')
    self._reader, __ = await asyncio.open_unix_connection(self._socket_path)

  async def update(self):
    mouse_event = await self._reader.read(32)
    message = event.from_message(struct.unpack("iiiiiiii", mouse_event))
    return message

  async def reset(self):
    await self._session.write('hit.selectionOnly', False)

    self.world = Controller.World(
        np.array(await (self._session.read('coordinateSystem'))))

    self.predefined_views = Controller.PredefinedViews(
        np.array(await (self._session.read('views.front'))))

    view_attribs = [
        'affine',
        'constructionPlane',
        'extents',
        # See note in Camera class.
        # 'fov',
        'frustum',
        'perspective',
        'target',
        'rotatable',
    ]

    camera = {}
    for attribute in view_attribs:
      logging.info('Reading client view.%s', attribute)
      result = await self._session.read(f'view.{attribute}')
      camera[attribute] = result
    logging.info('Creating camera')
    self.camera = Controller.Camera(**camera)

    await self._session.write('hit.selectionOnly', False)

  async def motion(self, event: event.MotionEvent):
    logging.info('Motion: %s', event)
    await self._session.write('motion', True)
    view_mtx = np.eye(4)
    view_mtx[3, :3] = np.array([-event.x, -event.z, event.y],
                               dtype=np.float32) * .001
    angles = np.array([event.pitch, event.yaw, -event.roll],
                      dtype=np.float32) * 0.005
    rotation_mtx = transform.Rotation.from_euler('xyz', angles, degrees=True)
    view_mtx[:3, :3] = rotation_mtx.as_matrix().reshape(3, 3)
    self.camera.affine = view_mtx @ self.camera.affine
    logging.debug('Affine matrix: %s', self.camera.affine.ravel())

    return await self._session.write('view.affine',
                                     self.camera.affine.T.ravel().tolist())
