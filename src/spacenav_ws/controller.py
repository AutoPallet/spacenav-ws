import asyncio
import dataclasses
import logging
import struct
from typing import Any

import numpy as np
from scipy.spatial import transform

from spacenav_ws.spacenav import MotionEvent, ButtonEvent, from_message
from spacenav_ws.wamp import WampSession, Prefix, Call, Subscribe, CallResult


class Mouse3d:
    """This bad boy doesn't do a damn thing right now!"""

    def __init__(self):
        self.id = "mouse0"


class Controller:
    """This one keeps track of the shared state between the client and the router! Or it should in any case.. It actually just completely ignores all client state except for a one time read out.. That should prolly be remedied"""

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

    def __init__(self, reader: asyncio.StreamReader, mouse: Mouse3d, wamp_state_handler: WampSession, client_metadata: dict):
        self.id = "controller0"
        self.client_metadata = client_metadata
        self.reader = reader
        self._mouse = mouse
        self.wamp_state_handler = wamp_state_handler

        self.wamp_state_handler.wamp.subscribe_handlers[self.controller_uri] = self.subscribe
        self.wamp_state_handler.wamp.call_handlers["wss://127.51.68.120/3dconnexion#update"] = self.client_update

        self.affine = None
        self.coordinate_system = None
        self.subscribed = False

    async def subscribe(self, msg: Subscribe):
        """When a subscription request for self.controller_uri comes in we start broadcasting!"""
        logging.info("handling subscribe %s", msg)
        self.subscribed = True

    async def client_update(self, controller_id: str, args: dict[str, Any]):
        # TODO start paying attention to at the very least focus events! But probably also other stuff
        logging.debug(f"Got update for '{controller_id}': {args}, THESE ARE DROPPED FOR NOW!")

    @property
    def controller_uri(self) -> str:
        return f"wss://127.51.68.120/3dconnexion3dcontroller/{self.id}"

    async def remote_write(self, *args):
        return await self.wamp_state_handler.client_rpc(self.controller_uri, "self:update", *args)

    async def remote_read(self, *args):
        return await self.wamp_state_handler.client_rpc(self.controller_uri, "self:read", *args)

    async def start_mouse_event_stream(self):
        """Right now we try to send every event to the client.. we should possibly maybe debounce?"""
        logging.info("Starting the mouse stream")
        while True:
            mouse_event = await self.reader.read(32)
            nums = struct.unpack("iiiiiiii", mouse_event)
            event = from_message(list(nums))
            if isinstance(event, ButtonEvent):
                logging.warning("Button presses are discarded for now! %s", event)
            elif isinstance(event, MotionEvent):
                if self.subscribed:
                    if self.client_metadata["name"] == "Onshape":
                        await self.update_onshape_client(event)
                    elif self.client_metadata["name"] == "WebThreeJS Sample":
                        await self.update_3dconnexion_client(event)
                    else:
                        logging.warning("Unknown client! Cannot send mouse events, client_metadata:%s", self.client_metadata)

    async def update_onshape_client(self, event: MotionEvent):
        # 1) pull down the current extents and model matrix
        extents = await self.remote_read("view.extents")
        flat = await self.remote_read("view.affine")
        curr_affine = np.asarray(flat, dtype=np.float32).reshape(4, 4)

        # TODO: This is not correct
        # 2) Handle rotation
        angles = np.array([event.pitch, event.yaw, -event.roll], dtype=np.float32) * 0.008
        rot_cam = transform.Rotation.from_euler("xyz", angles, degrees=True).as_matrix()
        rot_delta = np.eye(4, dtype=np.float32)
        rot_delta[:3, :3] = rot_cam
        rotated = rot_delta @ curr_affine
        # Rotate the model from _its_ perspective
        # angles = np.array([event.pitch, event.roll, event.yaw], dtype=np.float32) * 0.008
        # rot_cam = transform.Rotation.from_euler("xyz", angles, degrees=True).as_matrix()
        # rot_delta = np.eye(4, dtype=np.float32)
        # rot_delta[:3, :3] = rot_cam
        # rotated = curr_affine @ rot_delta

        # 3) Handle translations
        trans_delta = np.eye(4, dtype=np.float32)
        # Probably event.y doesn't do anything at all here!
        trans_delta[3, :3] = np.array([-event.x, -event.z, event.y], dtype=np.float32) * 0.0005
        new_affine = trans_delta @ rotated

        # why is zooming implemnted like this?!?
        zoom_delta = event.y * 0.0002
        scale = 1.0 + zoom_delta
        new_extents = [c * scale for c in extents]

        # Write back changes
        await self.remote_write("motion", True)
        await self.remote_write("view.affine", new_affine.reshape(-1).tolist())
        await self.remote_write("view.extents", new_extents)

    async def update_3dconnexion_client(self, event: MotionEvent):
        # 1) pull down the current extents and model matrix
        flat = await self.remote_read("view.affine")
        curr_affine = np.asarray(flat, dtype=np.float32).reshape(4, 4)

        # 2) Handle rotation
        # Rotate the model in the cameras perspective
        # angles = np.array([event.pitch, event.yaw, -event.roll], dtype=np.float32) * 0.008
        # rot_cam = transform.Rotation.from_euler("xyz", angles, degrees=True).as_matrix()
        # rot_delta = np.eye(4, dtype=np.float32)
        # rot_delta[:3, :3] = rot_cam
        # rotated = rot_delta @ curr_affine

        # Rotate the model from _its_ perspective
        angles = np.array([event.pitch, event.yaw, -event.roll], dtype=np.float32) * 0.008
        rot_cam = transform.Rotation.from_euler("xyz", angles, degrees=True).as_matrix()
        rot_delta = np.eye(4, dtype=np.float32)
        rot_delta[:3, :3] = rot_cam
        rotated = curr_affine @ rot_delta

        # 3) Handle translations
        trans_delta = np.eye(4, dtype=np.float32)
        # Probably event.y doesn't do anything at all here!
        trans_delta[3, :3] = np.array([-event.x, -event.z, event.y], dtype=np.float32) * 0.001
        new_affine = trans_delta @ rotated

        # Write back changes
        await self.remote_write("motion", True)
        await self.remote_write("view.affine", new_affine.reshape(-1).tolist())


async def create_mouse_controller(wamp_state_handler: WampSession, spacenav_reader: asyncio.StreamReader):
    await wamp_state_handler.wamp.begin()
    # The first three messages are typically prefix setters!
    msg = await wamp_state_handler.wamp.next_message()
    while isinstance(msg, Prefix):
        await wamp_state_handler.wamp.run_message_handler(msg)
        msg = await wamp_state_handler.wamp.next_message()

    # The first call after the prefixes must be 'create mouse'
    assert isinstance(msg, Call)
    assert msg.proc_uri == "3dx_rpc:create" and msg.args[0] == "3dconnexion:3dmouse"
    mouse = Mouse3d()
    logging.info(f'Created 3d mouse "{mouse.id}" for version {msg.args[1]}')
    await wamp_state_handler.wamp.send_message(CallResult(msg.call_id, {"connexion": mouse.id}))

    # And the second call after the prefixes must be 'create controller'
    msg = await wamp_state_handler.wamp.next_message()
    assert isinstance(msg, Call)
    assert msg.proc_uri == "3dx_rpc:create" and msg.args[0] == "3dconnexion:3dcontroller" and msg.args[1] == mouse.id
    metadata = msg.args[2]
    ctrl = Controller(spacenav_reader, mouse, wamp_state_handler, metadata)
    logging.info(f'Created controller "{ctrl.id}" for mouse "{mouse.id}", for client "{metadata["name"]}", version "{metadata["version"]}"')

    await wamp_state_handler.wamp.send_message(CallResult(msg.call_id, {"instance": ctrl.id}))
    return ctrl
