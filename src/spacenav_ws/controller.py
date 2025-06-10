import asyncio
import logging
import struct
import time
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
    """Manage shared state and event streaming between a local 3D mouse and a remote client.

    This class subscribes clients over WAMP, tracks focus/subscription state,
    reads raw 3D mouse data from an asyncio.StreamReader, and forwards
    MotionEvent/ButtonEvent updates back to the client via RPC. It also
    provides utility methods for affine‐pivot calculations and generic
    remote_read/write operations.

    Args:
        reader (asyncio.StreamReader):
            Asynchronous stream reader for receiving raw 3D mouse packets.
        _ (Mouse3d):
            Doesn't do anything.. things should be restructured so that it does probably.
        wamp_state_handler (WampSession):
            WAMP session handler that manages subscriptions and RPC calls.
        client_metadata (dict):
            Metadata about the connected client (e.g. its name and capabilities).

    Attributes:
        id (str):
            Unique identifier for this controller instance (defaults to "controller0").
        client_metadata (dict):
            Same as the constructor arg: information about the client.
        reader (asyncio.StreamReader):
            Stream reader for incoming mouse event bytes.
        wamp_state_handler (WampSession):
            WAMP session object for subscribing and remote RPC.
        subscribed (bool):
            True once the client has subscribed to this controller's URI.
        focus (bool):
            True when this controller is in focus and should send events.
        latest_motion_event: MotionEvent | None:
            The latest motion event received from the mouse.
        latest_motion_event_time: float:
            The timestamp of the latest motion event received from the mouse.
        next_send_time: float
    """

    def __init__(self, reader: asyncio.StreamReader, _: Mouse3d, wamp_state_handler: WampSession, client_metadata: dict):
        self.id = "controller0"
        self.client_metadata = client_metadata
        self.reader = reader
        self.wamp_state_handler = wamp_state_handler

        self.wamp_state_handler.wamp.subscribe_handlers[self.controller_uri] = self.subscribe
        self.wamp_state_handler.wamp.call_handlers["wss://127.51.68.120/3dconnexion#update"] = self.client_update

        self.subscribed = False
        self.focus = False

        # New state variables for two-loop system
        self.latest_motion_event: MotionEvent | None = None
        self.latest_motion_event_time: float = 0.0
        self.next_send_time: float = 0.0

    async def subscribe(self, msg: Subscribe):
        """When a subscription request for self.controller_uri comes in we start broadcasting!"""
        logging.info("handling subscribe %s", msg)
        self.subscribed = True
        self.focus = True

    async def client_update(self, controller_id: str, args: dict[str, Any]):
        # TODO Maybe use some more of this data that the client sends our way?
        logging.debug("Got update for '%s': %s, THESE ARE DROPPED FOR NOW!", controller_id, args)
        if (focus := args.get("focus")) is not None:
            self.focus = focus

    @property
    def controller_uri(self) -> str:
        return f"wss://127.51.68.120/3dconnexion3dcontroller/{self.id}"

    async def remote_write(self, *args):
        return await self.wamp_state_handler.client_rpc(self.controller_uri, "self:update", *args)

    async def remote_read(self, *args):
        return await self.wamp_state_handler.client_rpc(self.controller_uri, "self:read", *args)

    async def handle_button_event(self, event: ButtonEvent):
        """Resets the view on a button press."""
        logging.info("Handling button event: %s", event)
        model_extents = await self.remote_read("model.extents")
        await self.remote_write("view.affine", await self.remote_read("views.front"))
        await self.remote_write("view.extents", [c * 1.2 for c in model_extents])

    async def read_mouse_events_loop(self):
        """Reads events from the spacenav socket and signals the sender loop."""
        logging.info("Starting to read mouse events.")
        while True:
            try:
                mouse_event_bytes = await self.reader.readexactly(32)
                nums = struct.unpack("iiiiiiii", mouse_event_bytes)
                event = from_message(list(nums))

                if not (self.focus and self.subscribed and self.client_metadata["name"] in ["Onshape", "WebThreeJS Sample"]):
                    continue

                if isinstance(event, ButtonEvent):
                    await self.handle_button_event(event)
                elif isinstance(event, MotionEvent):
                    self.latest_motion_event = event
                    now = time.monotonic()
                    self.latest_motion_event_time = now
            except (asyncio.IncompleteReadError, ConnectionResetError) as e:
                logging.warning("Connection to spacenavd lost: %s", e)
                break
            except Exception as e:
                logging.error("Unexpected error in mouse reading loop: %s", e, exc_info=True)
                break

    async def send_updates_loop(self):
        """Sends motion updates to the client at a consistent 60Hz while in motion."""
        TARGET_RATE = 0.016
        TARGET_DELAY = 0.002  # How long after the event should we ideally send? Should be larger than OS scheduler jitter.
        ADJUST_KP = 0.1  # How much later should we send if we were too early?

        logging.info("Starting to send updates to client.")
        was_stopped = True
        while True:
            if self.next_send_time < time.monotonic():
                # Something got desynchronized.
                self.next_send_time = time.monotonic() + TARGET_RATE

            await asyncio.sleep(self.next_send_time - time.monotonic())

            self.next_send_time += TARGET_RATE
            time_since_event = self.next_send_time - (self.latest_motion_event_time + TARGET_DELAY)
            periods_since_event = round(time_since_event / TARGET_RATE)
            ideal_send_time = self.latest_motion_event_time + periods_since_event * TARGET_RATE + TARGET_DELAY

            error = ideal_send_time - self.next_send_time
            self.next_send_time += error * ADJUST_KP

            if self.latest_motion_event is None:
                continue

            # Stop sending updates if events from the mouse are extremely stale (e.g., spacenavd died)
            if (time.monotonic() - self.latest_motion_event_time) > 2.0:
                was_stopped = True
                continue

            is_stopped_now = self.latest_motion_event.is_stopped()

            # Send an update if we are moving, or if we just stopped (to send the final zero-motion event).
            if not (is_stopped_now and was_stopped):
                if self.focus and self.subscribed:
                    await self.update_client(self.latest_motion_event)

            was_stopped = is_stopped_now

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

    async def update_client(self, event: MotionEvent):
        """
        This send mouse events over to the client. Currently just a few properties are used but more are avaialable:
        view.target, view.constructionPlane, view.extents, view.affine, view.perspective, model.extents, selection.empty, selection.extents, hit.lookat, views.front

        """
        model_extents = await self.remote_read("model.extents")

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
        angles = np.array([event.pitch, event.yaw, -event.roll]) * 0.02
        R_delta_cam = transform.Rotation.from_euler("xyz", angles, degrees=True).as_matrix()
        R_world = R_cam @ R_delta_cam @ R_cam.T

        rot_delta = np.eye(4, dtype=np.float32)
        rot_delta[:3, :3] = R_world
        trans_delta = np.eye(4, dtype=np.float32)
        trans_delta[3, :3] = np.array([-event.x, -event.z, event.y], dtype=np.float32) * 0.0001

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


async def create_mouse_controller(wamp_state_handler: WampSession, spacenav_reader: asyncio.StreamReader) -> Controller:
    """
    This takes in an active websocket wrapped in a wampsession, it consumes the first couple of messages that form a sort of pseudo handshake..
    When all is said is done it returns an active controller!
    """
    await wamp_state_handler.wamp.begin()
    # The first three messages are typically prefix setters!
    msg = await wamp_state_handler.wamp.next_message()
    while isinstance(msg, Prefix):
        await wamp_state_handler.wamp.run_message_handler(msg)
        msg = await wamp_state_handler.wamp.next_message()

    # The first call after the prefixes must be 'create mouse'
    assert isinstance(msg, Call)
    assert msg.proc_uri == "3dx_rpc:create" and msg.args[0] == "3dconnexion:3dmouse"
    mouse = Mouse3d()  # There is really no point to this lol
    logging.info(f'Created 3d mouse "{mouse.id}" for version {msg.args[1]}')
    await wamp_state_handler.wamp.send_message(CallResult(msg.call_id, {"connexion": mouse.id}))

    # And the second call after the prefixes must be 'create controller'
    msg = await wamp_state_handler.wamp.next_message()
    assert isinstance(msg, Call)
    assert msg.proc_uri == "3dx_rpc:create" and msg.args[0] == "3dconnexion:3dcontroller" and msg.args[1] == mouse.id
    metadata = msg.args[2]
    controller = Controller(spacenav_reader, mouse, wamp_state_handler, metadata)
    logging.info(f'Created controller "{controller.id}" for mouse "{mouse.id}", for client "{metadata["name"]}", version "{metadata["version"]}"')

    await wamp_state_handler.wamp.send_message(CallResult(msg.call_id, {"instance": controller.id}))
    return controller
