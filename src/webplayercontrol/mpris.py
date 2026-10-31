"""Interaction with MPRIS players."""

import importlib.resources
from asyncio import Event
from contextlib import suppress
from dataclasses import dataclass
from io import BytesIO
from operator import attrgetter
from typing import TYPE_CHECKING, TypedDict, cast

from dbus_fast import DBusError, Variant
from dbus_fast.aio import MessageBus, ProxyInterface
from dbus_fast.introspection import Node

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

_BUS_NAME = "org.freedesktop.DBus"
"""The bus name of the message bus interface."""

_BUS_INTERFACE = "org.freedesktop.DBus"
"""The interface name of the message bus interface."""

_BUS_OBJECT = "/org/freedesktop/DBus"
"""The object path of the message bus interface."""

_DBUS_PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"
"""The interface name of the properties interface."""

MPRIS_NAME_PREFIX = "org.mpris.MediaPlayer2."
"""The prefix of the bus names of all MPRIS media players."""

_MPRIS_INTERFACE = "org.mpris.MediaPlayer2"
"""The interface name of the top-level MPRIS application interface."""

_MPRIS_PLAYER_INTERFACE = "org.mpris.MediaPlayer2.Player"
"""The interface name of the MPRIS player interface."""

_MPRIS_OBJECT = "/org/mpris/MediaPlayer2"
"""The object path of all MPRIS-related objects."""


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
        "_player": "The player proxy interface.",
        "_properties": "The properties proxy interface.",
        "changed": "An event that is set at creation and each time something changes.",
        "snapshot": "The latest state snapshot.",
    }

    _player: ProxyInterface
    _properties: ProxyInterface
    changed: Event
    snapshot: Snapshot

    def __init__(
        self,
        player: ProxyInterface,
        properties: ProxyInterface,
        values: dict[str, Variant],
    ) -> None:
        """
        Construct a new Monitor.

        External code should not call this. It should call App.monitor instead.

        :param player: The player proxy interface.
        :param properties: The properties proxy interface.
        :param values: The initial values of all the properties.
        """
        self._player = player
        self._properties = properties
        self.changed = Event()
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
        print("In close")  # noqa: T201
        self._properties.off_properties_changed(self._properties_changed)  # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
        self._player.off_seeked(self._seeked)  # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]

    def _properties_changed(
        self,
        intf: str,
        change: dict[str, Variant],
        invalidated: list[str],
    ) -> None:
        """
        Handle the PropertiesChanged event.

        :param intf: The interface on which properties changed.
        :param change: The new values of the properties that changed.
        :param invalidated: The properties whose values changed but are not provided.
        """
        print(f"In _properties_changed({intf=}, {change=}, {invalidated=})")  # noqa: T201
        if intf == _MPRIS_PLAYER_INTERFACE:
            self._apply_change(change)

    def _seeked(self, position: int) -> None:
        """
        Handle the Seeked event.

        :param position: The new position.
        """
        print(f"In _seeked({position=})")  # noqa: T201
        self._apply_change({"Position": Variant("x", position)})

    def _apply_change(self, change: dict[str, Variant]) -> None:
        """
        Handle an event that produces a dictionary of properties to change.

        :param change: The change.
        """
        new_snapshot = self.snapshot.apply_change(change)
        if new_snapshot != self.snapshot:
            print("  Snapshot differs, setting changed event")  # noqa: T201
            self.snapshot = new_snapshot
            self.changed.set()


class App:
    """The application-wide shared MPRIS-related state."""

    __slots__ = {
        "_bus": "The D-Bus message bus.",
        "_bus_interface": "The message bus interface of the message bus object.",
        "_dbus_xml": "The introspection data for org.freedesktop.DBus and family.",
        "_mpris_xml": "The introspection data for org.mpris.MediaPlayer2 and family.",
    }

    _bus: MessageBus
    _bus_interface: ProxyInterface
    _dbus_xml: Node
    _mpris_xml: Node

    @staticmethod
    async def create() -> App:
        """
        Construct a new App.

        :return: The new App.
        """
        bus = MessageBus()
        await bus.connect()
        return App(bus)

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

    async def aclose(self) -> None:
        """Shut down the App."""
        self._bus.disconnect()
        await self._bus.wait_for_disconnect()

    async def list_players(self) -> AsyncGenerator[tuple[str, str]]:
        """
        List all players currently in existence.

        :return: A sequence of (object-path, display-name) pairs.
        """
        names = cast("list[str]", await self._bus_interface.call_list_names())  # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
        for name in names:
            if name.startswith(MPRIS_NAME_PREFIX):
                with suppress(DBusError):
                    obj = self._bus.get_proxy_object(
                        name,
                        _MPRIS_OBJECT,
                        self._mpris_xml,
                    )
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

        :param name: The object path to the player.
        :return: A monitoring object.
        """
        obj = self._bus.get_proxy_object(name, _MPRIS_OBJECT, self._mpris_xml)
        player = obj.get_interface(_MPRIS_PLAYER_INTERFACE)
        obj = self._bus.get_proxy_object(name, _MPRIS_OBJECT, self._dbus_xml)
        props = obj.get_interface(_DBUS_PROPERTIES_INTERFACE)
        prop_values = cast(
            "dict[str, Variant]",
            await props.call_get_all(_MPRIS_PLAYER_INTERFACE),  # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
        )
        return Monitor(player, props, prop_values)
