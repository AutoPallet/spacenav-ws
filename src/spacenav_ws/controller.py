import asyncio
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
            if self.subscribed:
                if self.client_metadata["name"] in ["Onshape", "WebThreeJS Sample"]:
                    await self.update_client(event)
                else:
                    logging.warning("Unknown client! Cannot send mouse events, client_metadata:%s", self.client_metadata)

    @staticmethod
    def get_affine_pivot_matrices(model_extents):
        min_pt = np.array(model_extents[0:3], dtype=np.float32)
        max_pt = np.array(model_extents[3:6], dtype=np.float32)
        pivot = (min_pt + max_pt) * 0.5

        pivot_pos = np.eye(4, dtype=np.float32)
        pivot_pos[3, :3] = pivot
        pivot_neg = np.eye(4, dtype=np.float32)
        pivot_neg[3, :3] = -pivot
        return pivot_pos, pivot_neg

    async def update_client(self, event: MotionEvent | ButtonEvent):
        """
        This send mouse events over to the client. Currently just a few properties are used but more are avaialable:
        view.target, view.constructionPlane, view.extents, view.affine, view.perspective, model.extents, selection.empty, selection.extents, hit.lookat, views.front

        """
        model_extents = await self.remote_read("model.extents")

        if isinstance(event, ButtonEvent):
            await self.remote_write("view.affine", await self.remote_read("views.front"))
            await self.remote_write("view.extents", [c * 1.2 for c in model_extents])
            return

        # 1) pull down the current extents and model matrix
        perspective = await self.remote_read("view.perspective")
        curr_affine = np.asarray(await self.remote_read("view.affine"), dtype=np.float32).reshape(4, 4)

        # This (transpose of top left quadrant) is the correct way to get the rotation matrix of the camera but it is unstable.. Either of the below methods works fine though.
        R_cam = curr_affine[:3, :3].T
        # cam2world = np.linalg.inv(curr_affine)
        # R_cam = cam2world[:3, :3]
        U, _, Vt = np.linalg.svd(R_cam)
        R_cam = U @ Vt

        # 2) Seperately calculate rotation and translation matrices
        angles = np.array([event.pitch, event.yaw, -event.roll]) * 0.008
        R_delta_cam = transform.Rotation.from_euler("xyz", angles, degrees=True).as_matrix()
        R_world = R_cam @ R_delta_cam @ R_cam.T
        rot_delta = np.eye(4, dtype=np.float32)
        rot_delta[:3, :3] = R_world
        trans_delta = np.eye(4, dtype=np.float32)
        trans_delta[3, :3] = np.array([-event.x, -event.z, event.y], dtype=np.float32) * 0.0005

        # 3) Apply changes to the ModelViewProjection matrix
        pivot_pos, pivot_neg = self.get_affine_pivot_matrices(model_extents)
        new_affine = trans_delta @ curr_affine @ (pivot_neg @ rot_delta @ pivot_pos)

        # Write back changes and optionally update extents if the projection is orthographic!
        if not perspective:
            extents = await self.remote_read("view.extents")
            zoom_delta = event.y * 0.0002
            scale = 1.0 + zoom_delta
            new_extents = [c * scale for c in extents]
            await self.remote_write("motion", True)
            await self.remote_write("view.extents", new_extents)
        else:
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
