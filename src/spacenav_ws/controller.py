import asyncio
import dataclasses
import logging
import struct
from typing import Any

import numpy as np
from scipy.spatial import transform

import spacenav_ws.spacenav
import spacenav_ws.wamp


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

    def __init__(self, reader: asyncio.StreamReader, mouse: Mouse3d, wamp_state_handler: spacenav_ws.wamp.WampSession):
        self.id = "controller0"
        self.reader = reader
        self._mouse = mouse
        self.wamp_state_handler = wamp_state_handler

        self.wamp_state_handler.wamp.subscribe_handlers[self.controller_uri] = self.subscribe
        self.wamp_state_handler.wamp.call_handlers["wss://127.51.68.120/3dconnexion#update"] = self.client_update

        self.affine = None
        self.coordinate_system = None
        self.subscribed = False

    async def subscribe(self):
        """When a subscription request for self.controller_uri comes in we start broadcasting!"""
        # await self.initialize()
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
            event = spacenav_ws.spacenav.from_message(list(nums))
            if isinstance(event, spacenav_ws.spacenav.ButtonEvent):
                logging.warning("Button presses are discarded for now! %s", event)
            elif isinstance(event, spacenav_ws.spacenav.MotionEvent):
                if self.subscribed:
                    await self.send_mouse_event_to_client(event)

    async def send_mouse_event_to_client(self, event: spacenav_ws.spacenav.MotionEvent):
        # 1) pull down the current extents and model matrix
        extents = await self.remote_read("view.extents")
        flat = await self.remote_read("view.affine")
        curr_affine = np.asarray(flat, dtype=np.float32).reshape(4, 4)

        # TODO: 
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


async def create_mouse_controller(wamp_state_handler: spacenav_ws.wamp.WampSession, spacenav_reader: asyncio.StreamReader):
    await wamp_state_handler.wamp.begin()
    # The first three messages are typically prefix setters!
    msg = await wamp_state_handler.wamp.next_message()
    while isinstance(msg, spacenav_ws.wamp.Prefix):
        await wamp_state_handler.wamp.run_message_handler(msg)
        msg = await wamp_state_handler.wamp.next_message()

    # The first call after the prefixes must be 'create mouse'
    assert isinstance(msg, spacenav_ws.wamp.Call)
    assert msg.proc_uri == "3dx_rpc:create" and msg.args[0] == "3dconnexion:3dmouse"
    mouse = Mouse3d()
    logging.info(f'Created 3d mouse "{mouse.id}" for version {msg.args[1]}')
    await wamp_state_handler.wamp.send_message(spacenav_ws.wamp.CallResult(msg.call_id, {"connexion": mouse.id}))

    # And the second call after the prefixes must be 'create controller'
    msg = await wamp_state_handler.wamp.next_message()
    assert isinstance(msg, spacenav_ws.wamp.Call)
    assert msg.proc_uri == "3dx_rpc:create" and msg.args[0] == "3dconnexion:3dcontroller" and msg.args[1] == mouse.id
    metadata = msg.args[2]
    ctrl = Controller(spacenav_reader, mouse, wamp_state_handler)
    logging.info(f'Created controller "{ctrl.id}" for mouse "{mouse.id}", for client "{metadata["name"]}", version "{metadata["version"]}"')

    await wamp_state_handler.wamp.send_message(spacenav_ws.wamp.CallResult(msg.call_id, {"instance": ctrl.id}))
    return ctrl
