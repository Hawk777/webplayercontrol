"""Interaction with MPRIS players."""

import importlib.resources
from asyncio import Event
from contextlib import AsyncExitStack, aclosing, contextmanager, suppress
from dataclasses import dataclass
from io import BytesIO
from operator import attrgetter
from typing import TYPE_CHECKING, TypedDict, cast

from dbus_fast import DBusError, MessageFlag, Variant
from dbus_fast.aio import MessageBus, ProxyInterface
from dbus_fast.introspection import Node

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable, Generator

_BUS_NAME = "org.freedesktop.DBus"
"""The bus name of the message bus interface."""

_BUS_INTERFACE = "org.freedesktop.DBus"
"""The interface name of the message bus interface."""

_BUS_OBJECT = "/org/freedesktop/DBus"
"""The object path of the message bus interface."""

_DBUS_PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"
"""The interface name of the properties interface."""

_MPRIS_NAME_PREFIX = "org.mpris.MediaPlayer2."
"""The prefix of the bus names of all MPRIS media players."""

_MPRIS_INTERFACE = "org.mpris.MediaPlayer2"
"""The interface name of the top-level MPRIS application interface."""

_MPRIS_PLAYER_INTERFACE = "org.mpris.MediaPlayer2.Player"
"""The interface name of the MPRIS player interface."""

_MPRIS_OBJECT = "/org/mpris/MediaPlayer2"
"""The object path of all MPRIS-related objects."""


class MPRISError(Exception):
    """The base class of all errors raised by MPRIS operations."""

    __slots__ = {
        "object_path": "The object path.",
    }

    object_path: str

    def __init__(self, msg: str, object_path: str) -> None:
        """
        Construct a new MPRISError.

        :param msg: The error message.
        :param object_path: The object path.
        """
        super().__init__(msg)
        self.object_path = object_path


class NotFoundError(MPRISError):
    """Raised if there is no media player at the requested object path."""

    __slots__ = ()

    def __init__(self, object_path: str) -> None:
        """
        Construct a new NotFound.

        :param object_path: The object path.
        """
        super().__init__(f"No media player found at “{object_path}”", object_path)


class AccessDeniedError(MPRISError):
    """Raised if we are not permitted to talk to a media player."""

    __slots__ = ()

    def __init__(self, object_path: str) -> None:
        """
        Construct a new AccessDenied.

        :param object_path: The object path.
        """
        super().__init__(
            f"Access to media player at “{object_path}” denied",
            object_path,
        )


@contextmanager
def _wrap_exceptions(object_path: str) -> Generator[None]:
    """
    Wrap D-Bus errors in MPRIS errors.

    :param object_path: The object path being communicated with.
    """
    try:
        yield
    except DBusError as exp:
        match exp.type:
            case (
                "org.freedesktop.DBus.Error.ServiceUnknown"
                | "org.freedesktop.DBus.Error.NameHasNoOwner"
            ):
                raise NotFoundError(object_path) from exp
            case (
                "org.freedesktop.DBus.Error.AccessDenied"
                | "org.freedesktop.DBus.Error.InteractiveAuthorizationRequired"
            ):
                raise AccessDeniedError(object_path) from exp
            case _:
                msg = f"General error accessing media player at “{object_path}”"
                raise MPRISError(msg, object_path) from exp


class Capabilities(TypedDict):
    """The operations that a client is allowed to do with a media player."""

    goPrevious: bool
    stop: bool
    pause: bool
    play: bool
    goNext: bool
    seek: bool


@dataclass(frozen=True)
class Snapshot:
    """A snapshot of the current state of a player."""

    __slots__ = {
        "can_control": "Whether controls in general are enabled.",
        "can_go_next": "Whether the Go Next operation is enabled.",
        "can_go_previous": "Whether the Go Previous operation is enabled.",
        "can_pause": "Whether the Pause operation is enabled.",
        "can_play": "Whether the Play operation is enabled.",
        "can_seek": "Whether the Seek operation is enabled.",
        "length": "The length of the clip, in microseconds.",
        "playing": "Whether the clip is playing.",
        "position": "The playback position, in microseconds.",
        "rate": "The playback rate.",
        "title": "The title of the currently playing clip.",
        "track_id": "The object path that acts as a track ID.",
        "volume": "The volume level, between 0 and 1.",
    }

    can_control: bool
    can_go_next: bool
    can_go_previous: bool
    can_pause: bool
    can_play: bool
    can_seek: bool
    length: int
    playing: bool
    position: int
    rate: float
    title: str
    track_id: str
    volume: float

    @staticmethod
    def _get_one_change[T](
        change: dict[str, Variant],
        field: str,
        old: T,
        typ: type[T],
    ) -> T:
        """
        Get the new value of one field.

        :param change: The value-change dictionary.
        :param field: The name of the field in :param change: that may apply.
        :param old: The old value of the field.
        :param typ: The type of the field.
        :return: The new value.
        """
        if (new_value := change.get(field)) is None:
            return old
        if not isinstance(new_value.value, typ):
            return old
        return new_value.value

    def apply_change(self, change: dict[str, Variant]) -> Snapshot:
        """
        Apply a change reported via a PropertiesChanged signal.

        :param change: The changed property values.
        :return: The new Snapshot.
        """
        can_control = self._get_one_change(change, "CanControl", self.can_control, bool)
        can_go_next = self._get_one_change(change, "CanGoNext", self.can_go_next, bool)
        can_go_previous = self._get_one_change(
            change,
            "CanGoPrevious",
            self.can_go_previous,
            bool,
        )
        can_play = self._get_one_change(change, "CanPlay", self.can_play, bool)
        can_pause = self._get_one_change(change, "CanPause", self.can_pause, bool)
        can_seek = self._get_one_change(change, "CanSeek", self.can_seek, bool)
        playing = self.playing
        if (status := change.get("PlaybackStatus")) is not None:
            playing = status.value == "Playing"
        position = self._get_one_change(change, "Position", self.position, int)
        rate = self._get_one_change(change, "Rate", self.rate, float)
        length = self.length
        title = self.title
        track_id = self.track_id
        if (metadata := change.get("Metadata")) is not None and isinstance(
            metadata.value,
            dict,
        ):
            md = cast("dict[str, Variant]", metadata.value)  # pyright: ignore [reportUnknownMemberType]
            if (new_length := md.get("mpris:length")) is not None and isinstance(
                new_length.value,
                int,
            ):
                length = new_length.value
            if (new_title := md.get("xesam:title")) is not None and isinstance(
                new_title.value,
                str,
            ):
                title = new_title.value
            if (new_track_id := md.get("mpris:trackid")) is not None and isinstance(
                new_track_id.value,
                str,
            ):
                track_id = new_track_id.value
        volume = self._get_one_change(change, "Volume", self.volume, float)
        return Snapshot(
            can_control=can_control,
            can_go_next=can_go_next,
            can_go_previous=can_go_previous,
            can_pause=can_pause,
            can_play=can_play,
            can_seek=can_seek,
            length=length,
            playing=playing,
            position=position,
            rate=rate,
            title=title,
            track_id=track_id,
            volume=volume,
        )

    def _diff_sse_value[T](
        self,
        old: Snapshot | None,
        event: bytes,
        get: Callable[[Snapshot], T],
        to: BytesIO,
    ) -> None:
        """
        Generate the SSE event for one field if it has changed since an old snapshot.

        :param old: The old snapshot to compare to.
        :param event: The event name to use in the SSE event, if changed.
        :param get: A function that, when given a Snapshot, returns the field value.
        :param to: The stream to append the generated SSE event to, if any.
        """
        value = get(self)
        changed = old is None or value != get(old)
        if not changed:
            return
        if isinstance(value, bool):
            rendered = b"1" if value else b"0"
        else:
            rendered = str(value).encode("UTF-8")
        rendered = rendered.replace(b"\r\n", b"\n")
        rendered = rendered.replace(b"\r", b"\n")
        rendered = rendered.replace(b"\n", b"\ndata:")
        to.write(b"event:")
        to.write(event)
        to.write(b"\ndata:")
        to.write(rendered)
        to.write(b"\n\n")

    def diff_sse(self, old: Snapshot | None) -> bytes:
        """
        Generate a diff from an old snapshot as an SSE sequence.

        :param old: The old snapshot to compare to.
        :return: The differences, as a sequence of SSE events.
        """
        bio = BytesIO()
        self._diff_sse_value(
            old,
            b"canGoPrevious",
            lambda s: s.can_control and s.can_go_previous,
            bio,
        )
        self._diff_sse_value(
            old,
            b"canSeekBack",
            lambda s: s.can_control and s.can_seek,
            bio,
        )
        self._diff_sse_value(old, b"canStop", attrgetter("can_control"), bio)
        self._diff_sse_value(
            old,
            b"canPause",
            lambda s: s.can_control and s.can_pause and s.playing,
            bio,
        )
        self._diff_sse_value(
            old,
            b"canPlay",
            lambda s: s.can_control and s.can_play and not s.playing,
            bio,
        )
        self._diff_sse_value(
            old,
            b"canSeekForward",
            lambda s: s.can_control and s.can_seek,
            bio,
        )
        self._diff_sse_value(
            old,
            b"canGoNext",
            lambda s: s.can_control and s.can_go_next,
            bio,
        )
        self._diff_sse_value(old, b"length", attrgetter("length"), bio)
        self._diff_sse_value(old, b"position", attrgetter("position"), bio)
        self._diff_sse_value(
            old,
            b"rate",
            lambda s: s.rate if s.playing else 0.0,
            bio,
        )
        self._diff_sse_value(old, b"volume", attrgetter("volume"), bio)
        # Do title last because, at startup, it tells the client that all information
        # has been delivered.
        self._diff_sse_value(old, b"title", attrgetter("title"), bio)
        return bio.getvalue()


class Monitor:
    """A monitor of a single player."""

    __slots__ = {
        "_monitor_set": "The set of monitors monitoring this player.",
        "_player": "The player proxy interface.",
        "_properties": "The properties proxy interface.",
        "changed": "An event that is set at creation and each time something changes.",
        "closed": "Whether this Monitor has been closed.",
        "snapshot": "The latest state snapshot.",
    }

    _monitor_set: set[Monitor]
    _player: ProxyInterface
    _properties: ProxyInterface
    changed: Event
    closed: bool
    snapshot: Snapshot

    def __init__(
        self,
        monitor_set: set[Monitor],
        player: ProxyInterface,
        properties: ProxyInterface,
        values: dict[str, Variant],
    ) -> None:
        """
        Construct a new Monitor.

        External code should not call this. It should call App.monitor instead.

        :param monitor_set: The set of monitors monitoring this player.
        :param player: The player proxy interface.
        :param properties: The properties proxy interface.
        :param values: The initial values of all the properties.
        """
        self._monitor_set = monitor_set
        self._player = player
        self._properties = properties
        self.changed = Event()
        self.closed = False
        self.snapshot = Snapshot(
            can_control=False,
            can_go_next=False,
            can_go_previous=False,
            can_pause=False,
            can_play=False,
            can_seek=False,
            length=0,
            playing=False,
            position=0,
            rate=0.0,
            title="",
            track_id="",
            volume=0.0,
        ).apply_change(values)
        properties.on_properties_changed(self._properties_changed)  # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
        player.on_seeked(self._seeked)  # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]

    def close(self) -> None:
        """Shut down a Monitor."""
        if self.closed:
            return
        self.closed = True
        self._properties.off_properties_changed(self._properties_changed)  # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
        self._player.off_seeked(self._seeked)  # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
        self.changed.set()
        self._monitor_set.remove(self)

    def _properties_changed(
        self,
        intf: str,
        change: dict[str, Variant],
        _invalidated: list[str],
    ) -> None:
        """
        Handle the PropertiesChanged event.

        :param intf: The interface on which properties changed.
        :param change: The new values of the properties that changed.
        :param _invalidated: The properties whose values changed but are not provided.
        """
        if intf == _MPRIS_PLAYER_INTERFACE:
            self._apply_change(change)

    def _seeked(self, position: int) -> None:
        """
        Handle the Seeked event.

        :param position: The new position.
        """
        self._apply_change({"Position": Variant("x", position)})

    def _apply_change(self, change: dict[str, Variant]) -> None:
        """
        Handle an event that produces a dictionary of properties to change.

        :param change: The change.
        """
        new_snapshot = self.snapshot.apply_change(change)
        if new_snapshot != self.snapshot:
            self.snapshot = new_snapshot
            self.changed.set()


class App:
    """The application-wide shared MPRIS-related state."""

    __slots__ = {
        "_bus": "The D-Bus message bus.",
        "_bus_interface": "The message bus interface of the message bus object.",
        "_dbus_xml": "The introspection data for org.freedesktop.DBus and family.",
        "_mpris_xml": "The introspection data for org.mpris.MediaPlayer2 and family.",
        "_players": "The unique bus names of players and the Monitors watching them.",
    }

    _bus: MessageBus
    _bus_interface: ProxyInterface
    _dbus_xml: Node
    _mpris_xml: Node
    _players: dict[str, set[Monitor]]

    @staticmethod
    async def create() -> App:
        """
        Construct a new App.

        :return: The new App.
        """
        async with AsyncExitStack() as stack:
            bus = MessageBus()
            await bus.connect()
            app = App(bus)
            stack.push_async_exit(aclosing(app))
            # I am a static method of App!
            # pylint: disable-next=protected-access
            await app._fill_players()
            stack.pop_all()
            return app

    def __init__(self, bus: MessageBus) -> None:
        """
        Construct a new App.

        This is meant for internal use. External code should use the create method
        instead.

        :param bus: The message bus.
        """
        self._bus = bus
        assert __package__ is not None
        self._dbus_xml = Node.parse(
            importlib.resources.read_text(__package__, "dbus.xml"),
        )
        obj = bus.get_proxy_object(_BUS_NAME, _BUS_OBJECT, self._dbus_xml)
        self._bus_interface = obj.get_interface(_BUS_INTERFACE)
        self._mpris_xml = Node.parse(
            importlib.resources.read_text(__package__, "mpris.xml"),
        )
        self._players = {}
        self._bus_interface.on_name_owner_changed(self._name_owner_changed)  # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]

    async def _fill_players(self) -> None:
        """
        Initialize the list of players.

        This must be invoked after the constructor completes, so that the
        NameOwnerChanged signal connection has already been established, closing a race
        in which a name comes into existence changes after ListNames has been called but
        before the NameOwnerChanged signal is connected, so it is never seen.
        """
        all_names = cast(
            "list[str]",
            await self._bus_interface.call_list_names(flags=MessageFlag.NO_AUTOSTART),  # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
        )
        for name in all_names:
            if not name.startswith(_MPRIS_NAME_PREFIX):
                continue
            try:
                owner = cast(
                    "str",
                    await self._bus_interface.call_get_name_owner(  # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
                        name,
                        flags=MessageFlag.NO_AUTOSTART,
                    ),
                )
                self._players[owner] = set()
            except DBusError as exp:
                if exp.type != "org.freedesktop.DBus.Error.NameHasNoOwner":
                    raise

    def _name_owner_changed(self, name: str, old_owner: str, new_owner: str) -> None:
        """
        Handle the NameOwnerChanged signal.

        :param name: The well-known name whose ownership changed.
        :param old_owner: The unique name of the old owner, or empty string if none.
        :param new_owner: The unique name of the new owner, or empty string if none.
        """
        if not name.startswith(_MPRIS_NAME_PREFIX):
            return
        if old_owner:
            monitors = self._players.pop(old_owner, set())
            while (monitor := next(iter(monitors), None)) is not None:
                monitor.close()
        if new_owner:
            self._players[new_owner] = set()

    async def aclose(self) -> None:
        """Shut down the App."""
        self._bus.disconnect()
        await self._bus.wait_for_disconnect()

    def has_player(self, name: str) -> bool:
        """
        Check whether a player with a given unique bus name exists.

        :param name: The unique bus name.
        :return: True if there is an MPRIS player at :param name:, or False if not.
        """
        return name in self._players

    async def list_players(self) -> AsyncGenerator[tuple[str, str]]:
        """
        List all players currently in existence.

        :return: A sequence of (unique-bus-name, display-name) pairs.
        """
        for name in self._players:
            with suppress(DBusError):
                obj = self._bus.get_proxy_object(name, _MPRIS_OBJECT, self._mpris_xml)
                intf = obj.get_interface(_MPRIS_INTERFACE)
                identity = cast("str", await intf.get_identity())  # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
                intf = obj.get_interface(_MPRIS_PLAYER_INTERFACE)
                metadata = cast("dict[str, Variant]", await intf.get_metadata())  # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
                title = metadata.get("xesam:title")
                if title is not None and isinstance(title.value, str):
                    display = f"{identity}: {title.value}"
                else:
                    display = identity
                yield name, display

    async def monitor(self, name: str) -> Monitor:
        """
        Monitor a player for changes in status.

        :param name: The unique bus name of the player.
        :return: A monitoring object.
        """
        try:
            monitors = self._players[name]
        except KeyError as exp:
            raise NotFoundError(name) from exp
        obj = self._bus.get_proxy_object(name, _MPRIS_OBJECT, self._mpris_xml)
        player = obj.get_interface(_MPRIS_PLAYER_INTERFACE)
        obj = self._bus.get_proxy_object(name, _MPRIS_OBJECT, self._dbus_xml)
        props = obj.get_interface(_DBUS_PROPERTIES_INTERFACE)
        with _wrap_exceptions(name):
            prop_values = cast(
                "dict[str, Variant]",
                await props.call_get_all(  # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
                    _MPRIS_PLAYER_INTERFACE,
                    flags=MessageFlag.NO_AUTOSTART,
                ),
            )
        mon = Monitor(monitors, player, props, prop_values)
        monitors.add(mon)
        return mon

    async def _command(
        self,
        name: str,
        action: Callable[[ProxyInterface], Awaitable[None]],
    ) -> None:
        """
        Execute a command on a player.

        :param name: The unique bus name.
        :param action: A callable that performs the action.
        """
        with _wrap_exceptions(name):
            obj = self._bus.get_proxy_object(name, _MPRIS_OBJECT, self._mpris_xml)
            player = obj.get_interface(_MPRIS_PLAYER_INTERFACE)
            await action(player)

    async def go_previous(self, name: str) -> None:
        """
        Move to the previous item in the playlist.

        :param name: The unique bus name of the player.
        """
        await self._command(name, lambda player: player.call_previous())  # pyright: ignore [reportAttributeAccessIssue, reportUnknownLambdaType, reportUnknownMemberType]

    async def seek_back(self, name: str) -> None:
        """
        Move five seconds backward in the current clip.

        :param name: The unique bus name of the player.
        """
        await self._command(name, lambda player: player.call_seek(-5_000_000))  # pyright: ignore [reportAttributeAccessIssue, reportUnknownLambdaType, reportUnknownMemberType]

    async def stop(self, name: str) -> None:
        """
        Stop playback.

        :param name: The unique bus name of the player.
        """
        await self._command(name, lambda player: player.call_stop())  # pyright: ignore [reportAttributeAccessIssue, reportUnknownLambdaType, reportUnknownMemberType]

    async def pause(self, name: str) -> None:
        """
        Pause playback.

        :param name: The unique bus name of the player.
        """
        await self._command(name, lambda player: player.call_pause())  # pyright: ignore [reportAttributeAccessIssue, reportUnknownLambdaType, reportUnknownMemberType]

    async def play(self, name: str) -> None:
        """
        Start or resume playback.

        :param name: The unique bus name of the player.
        """
        await self._command(name, lambda player: player.call_play())  # pyright: ignore [reportAttributeAccessIssue, reportUnknownLambdaType, reportUnknownMemberType]

    async def seek_forward(self, name: str) -> None:
        """
        Move five seconds forward in the current clip.

        :param name: The unique bus name of the player.
        """
        await self._command(name, lambda player: player.call_seek(5_000_000))  # pyright: ignore [reportAttributeAccessIssue, reportUnknownLambdaType, reportUnknownMemberType]

    async def go_next(self, name: str) -> None:
        """
        Move to the next item in the playlist.

        :param name: The unique bus name of the player.
        """
        await self._command(name, lambda player: player.call_next())  # pyright: ignore [reportAttributeAccessIssue, reportUnknownLambdaType, reportUnknownMemberType]


COMMANDS: dict[str, Callable[[App, str], Awaitable[None]]] = {
    "goPrevious": App.go_previous,
    "seekBack": App.seek_back,
    "stop": App.stop,
    "pause": App.pause,
    "play": App.play,
    "seekForward": App.seek_forward,
    "goNext": App.go_next,
}
"""
The commands that a client can request.

Each entry maps from a command name (i.e. last path segment in a POST URL) to a command
handler for that command.
"""
