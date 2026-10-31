"""The application entry point."""

import json
from asyncio import TaskGroup, wait_for
from contextlib import AsyncExitStack, closing, suppress
from typing import TYPE_CHECKING

from .assets import AssetHandler
from .mpris import MPRIS_NAME_PREFIX
from .mpris import App as MPRISApp
from .utils import acceptable, discard_request, send_error

if TYPE_CHECKING:
    from asgi_types import (
        ASGIReceiveCallable,
        ASGISendCallable,
        HTTPScope,
        LifespanScope,
        Scope,
    )

    from .mpris import Monitor

_KEEPALIVE_TIMEOUT = 15
"""How many seconds to wait before sending an empty SSE event."""


def _version_tuple(v: str) -> tuple[int, ...]:
    """
    Convert a version number into a tuple of its dot-separated components.

    :param v: The version number.
    :return: The version number components.
    """
    return (*(int(i) for i in v.split(".")),)


class _ClientDisconnectedError(Exception):
    """Raised internally in the status handler to handle a client disconnecting."""

    __slots__ = ()


class _App:
    """The shared state of the application."""

    __slots__ = {
        "_assets": "The asset handler.",
        "_mpris": "The MPRIS application object.",
    }

    _assets: AssetHandler
    _mpris: MPRISApp

    def __init__(self, assets: AssetHandler, mpris: MPRISApp) -> None:
        """
        Construct a new _App.

        :param assets: The asset handler.
        :param mpris: The MPRIS application object.
        """
        self._assets = assets
        self._mpris = mpris

    async def aclose(self) -> None:
        """Shut down the _App."""
        async with AsyncExitStack() as stack:
            stack.push_async_callback(self._mpris.aclose)

    @staticmethod
    async def run_lifespan(
        scope: LifespanScope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:
        """
        Run the lifespan protocol.

        :param scope: The scope.
        :param receive: The event-receive function.
        :param send: The event-send function.
        """
        while True:
            event = await receive()
            match event["type"]:
                case "lifespan.startup":
                    try:
                        await _App.run_lifespan_startup(scope)
                        await send({"type": "lifespan.startup.complete"})
                    # ruff: ignore[BLE001]  Intentional, changing reporting mechanism.
                    except Exception as exp:
                        await send(
                            {
                                "type": "lifespan.startup.failed",
                                "message": str(exp),
                            },
                        )
                        break
                case "lifespan.shutdown":
                    try:
                        await _App.run_lifespan_shutdown(scope)
                        await send({"type": "lifespan.shutdown.complete"})
                    # ruff: ignore[BLE001]  Intentional, changing reporting mechanism.
                    except Exception as exp:
                        await send(
                            {
                                "type": "lifespan.shutdown.failed",
                                "message": str(exp),
                            },
                        )
                    break
                case _:
                    pass

    @staticmethod
    async def run_lifespan_startup(scope: LifespanScope) -> None:
        """
        Handle the lifespan.startup event.

        :param scope: The scope.
        """
        # Sanity check versions.
        asgi_version = _version_tuple(scope["asgi"].get("version", "2.0"))
        if asgi_version < (3, 0):
            msg = "This application requires an ASGI ≥ 3.0 server"
            raise ValueError(msg)
        spec_version = _version_tuple(scope["asgi"].get("spec_version", "1.0"))
        if spec_version < (2, 0):
            msg = "This application requires a server with ASGI lifespan spec ≥ 2.0"
            raise ValueError(msg)

        # Set up the asset handler.
        assets = AssetHandler()

        # Set up the MPRIS application object.
        mpris = await MPRISApp.create()

        # Initialize the _App object and store it in the lifespan state.
        state = scope["state"]  # pyright: ignore [reportTypedDictNotRequiredAccess]
        state["webplayercontrol"] = _App(assets, mpris)

    @staticmethod
    async def run_lifespan_shutdown(scope: LifespanScope) -> None:
        """
        Handle the lifespan.shutdown event.

        :param scope: The scope.
        """
        # Close down the _App.
        state = scope["state"]  # pyright: ignore [reportTypedDictNotRequiredAccess]
        app = state["webplayercontrol"]
        del state["webplayercontrol"]
        await app.aclose()

    @staticmethod
    async def _redirect_add_slash(
        scope: HTTPScope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:
        """
        Redirect to the requested path plus a slash.

        :param scope: The scope.
        :param receive: The event-receive function.
        :param send: The event-send function.
        """
        # Receive and discard any body.
        await discard_request(receive)

        # Send the response.
        await send(
            {
                "type": "http.response.start",
                "status": 308,
                "headers": [(b"location", (scope["path"] + "/").encode("UTF-8"))],
            },
        )
        await send({"type": "http.response.body", "body": b""})

    async def _handle_players_list(
        self,
        scope: HTTPScope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:
        """
        Handle the URL that is exactly “players/”.

        This URL sends a list of available players as a JSON object of this shape:

        [["foo", "bar"], ["baz", "quux"]]

        where foo and baz are D-Bus object paths and bar and quux are human-readable
        display names.

        :param scope: The scope.
        :param receive: The event-receive function.
        :param send: The event-send function.
        """
        await discard_request(receive)
        if scope["method"] != "GET":
            await send_error(send, 405, "Method Not Allowed")
            return
        if not acceptable(b"application/json", scope):
            await send_error(send, 406, "Not Acceptable")
            return
        players = [i async for i in self._mpris.list_players()]
        players.sort(key=lambda i: i[1])
        players = json.dumps(players)
        players = players.encode("UTF-8")
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(players)).encode("ASCII")),
            (b"cache-control", b"no-store"),
        ]
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": headers,
            },
        )
        await send({"type": "http.response.body", "body": players})

    @staticmethod
    async def _handle_player_status_updates(
        monitor: Monitor,
        send: ASGISendCallable,
    ) -> None:
        """
        Send SSE events as the status of a player changes, or as keepalives are needed.

        :param monitor: The monitor.
        :param send: The send callable.
        """
        # Send the initial condition.
        last_snapshot = monitor.snapshot
        await send(
            {
                "type": "http.response.body",
                "body": last_snapshot.diff_sse(None),
                "more_body": True,
            },
        )
        print("initial condition sent")  # noqa: T201

        # Send updates or keepalives as needed.
        while True:
            # Wait until something changes or we reach the keepalive time.
            try:
                await wait_for(monitor.changed.wait(), _KEEPALIVE_TIMEOUT)
                changed = True
            except TimeoutError:
                changed = False

            # Send what we need to send.
            if changed:
                print("change detected")  # noqa: T201
                monitor.changed.clear()
                snapshot = monitor.snapshot
                await send(
                    {
                        "type": "http.response.body",
                        "body": snapshot.diff_sse(last_snapshot),
                        "more_body": True,
                    },
                )
                last_snapshot = snapshot
            else:
                print("keepalive")  # noqa: T201
                await send(
                    {"type": "http.response.body", "body": b":\n\n", "more_body": True},
                )

    @staticmethod
    async def _handle_player_status_disconnect(receive: ASGIReceiveCallable) -> None:
        """Raise an exception when a client disconnects from the status endpoint."""
        print("disconnect handler: start monitoring")  # noqa: T201
        while True:
            event = await receive()
            print(f"disconnect handler: got event {event}")  # noqa: T201
            if event["type"] == "http.disconnect":
                print("got disconnect")  # noqa: T201
                raise _ClientDisconnectedError

    async def _handle_player_status(
        self,
        player_id: str,
        scope: HTTPScope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:
        """
        Handle the players/<foo>/status URL.

        :param player_id: The player object path.
        :param scope: The scope.
        :param receive: The event-receive function.
        :param send: The event-send function.
        """
        # Receive and discard any request body.
        await discard_request(receive)

        # The status endpoint can only handle GET requests.
        if scope["method"] != "GET":
            await send_error(send, 405, "Method Not Allowed")
            return

        # The status endpoint always sends text/event-stream.
        if not acceptable(b"text/event-stream", scope):
            await send_error(send, 406, "Not Acceptable")
            return

        # Open a monitor.
        with closing(await self._mpris.monitor(player_id)) as monitor:
            # Send the headers.
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")],
                },
            )

            # Spawn tasks to send the data.
            with suppress(_ClientDisconnectedError):
                async with TaskGroup() as tg:
                    tg.create_task(self._handle_player_status_updates(monitor, send))
                    tg.create_task(self._handle_player_status_disconnect(receive))

    async def _handle_players_url(
        self,
        path: str,
        scope: HTTPScope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:
        """
        Handle URLs starting with players/.

        :param path: The remaining path, relative to players/.
        :param scope: The scope.
        :param receive: The event-receive function.
        :param send: The event-send function.
        """
        if path == "":
            await self._handle_players_list(scope, receive, send)
            return
        parts = path.split("/", 1)
        if len(parts) != 2:  # noqa: PLR2004
            await discard_request(receive)
            await send_error(send, 404, "Not Found")
            return
        player_id, resource = parts
        if not player_id.startswith(MPRIS_NAME_PREFIX):
            await discard_request(receive)
            await send_error(send, 404, "Not Found")
            return
        match resource:
            case "":
                await self._assets.handle(
                    "player.html",
                    b"no-store",
                    scope,
                    receive,
                    send,
                )
            case "status":
                await self._handle_player_status(player_id, scope, receive, send)
            case _:
                await discard_request(receive)
                await send_error(send, 404, "Not Found")

    async def run_http(
        self,
        scope: HTTPScope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:
        """
        Run the HTTP protocol.

        :param scope: The scope.
        :param receive: The event-receive function.
        :param send: The event-send function.
        """
        # Sanity check version.
        spec_version = _version_tuple(scope["asgi"].get("spec_version", "2.0"))
        if spec_version < (2, 0):
            msg = "This application requires a server with ASGI HTTP spec ≥ 2.0"
            raise ValueError(msg)

        # Sanity check and relativize path.
        root_path = scope["root_path"]
        path = scope["path"]
        if not path.startswith(root_path):
            msg = "path does not start with root_path"
            raise ValueError(msg)
        path = path[len(root_path) :]

        # If we are mounted at /foo, and that is the URL the user requested, redirect to
        # /foo/.
        if not root_path.endswith("/") and path == "":
            await self._redirect_add_slash(scope, receive, send)
            return

        # If the path doesn’t start with /, we don’t know how to deal with it. If it
        # does, remove the leading slash.
        if not path.startswith("/"):
            await discard_request(receive)
            await send_error(send, 404, "Not Found")
            return
        path = path[1:]

        # Send content-addressable static assets.
        if await self._assets.maybe_handle_cas(path, scope, receive, send):
            return

        # Handle other requests.
        if path == "":
            await self._assets.handle("index.html", b"max-age=60", scope, receive, send)
        elif path.startswith("players/"):
            path = path.removeprefix("players/")
            await self._handle_players_url(path, scope, receive, send)
        else:
            await discard_request(receive)
            await send_error(send, 404, "Not Found")


async def main(
    scope: Scope,
    receive: ASGIReceiveCallable,
    send: ASGISendCallable,
) -> None:
    """
    Run an ASGI scope.

    :param scope: The scope.
    :param receive: The event-receive function.
    :param send: The event-send function.
    """
    match scope["type"]:
        case "lifespan":
            await _App.run_lifespan(scope, receive, send)
        case "http":
            state = scope["state"]  # pyright: ignore [reportTypedDictNotRequiredAccess]
            app = state["webplayercontrol"]
            await app.run_http(scope, receive, send)
        case _:
            msg = f"Scope {scope['type']} not supported"
            raise ValueError(msg)
