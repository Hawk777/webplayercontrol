"""Handling of static asset files."""

import binascii
import importlib.resources
from base64 import b64encode, urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from hashlib import sha256
from logging import getLogger
from os import environ
from pathlib import Path
from typing import TYPE_CHECKING

from .utils import acceptable, discard_request, send_error, unmodified

if TYPE_CHECKING:
    from asgi_types import (
        ASGIReceiveCallable,
        ASGISendCallable,
        HTTPScope,
    )


@dataclass(frozen=True)
class _FileType:
    """Information about a particular file type."""

    __slots__ = {
        "mime_type": "The MIME type of files with this extension.",
        "template": "Whether this file type permits template substitutions.",
    }

    mime_type: bytes
    template: bool


_FILE_TYPES = {
    "css": _FileType(mime_type=b"text/css", template=False),
    "html": _FileType(mime_type=b"text/html", template=True),
    "js": _FileType(mime_type=b"text/javascript", template=False),
}
"""All known file types, by file extension."""


@dataclass
class _Asset:
    """Information about an individual asset."""

    __slots__ = {
        "content": "The content of the asset.",
        "etag": "The ETag header value.",
        "extracted": "The path to which the asset was extracted for pathsend.",
        "file_type": "The file type.",
    }

    content: bytes
    etag: bytes
    extracted: Path | None
    file_type: _FileType


class AssetHandler:
    """A handler that knows how to deal with static asset files."""

    __slots__ = {
        "_assets": "A mapping from hash value to _Asset object.",
        "_hashes": "A mapping from filename to hash value.",
        "_warned": "Whether the missing pathsend extension warning has been shown yet.",
    }

    _assets: dict[bytes, _Asset]
    _hashes: dict[str, bytes]
    _warned: bool

    def __init__(self) -> None:
        """Initialize the asset handler."""
        self._warned = False

        # Load all the assets into memory.
        by_name = {
            i.name: _Asset(
                content=i.read_bytes(),
                etag=b"",
                extracted=None,
                file_type=_FILE_TYPES[i.name.rsplit(".", 1)[-1]],
            )
            for i in (importlib.resources.files(__package__) / "assets").iterdir()
        }

        # Hash the ones that are not templates, because we already know their content.
        self._hashes = {
            k: sha256(v.content).digest()
            for k, v in by_name.items()
            if not v.file_type.template
        }

        # Preprocess the ones that are templates; they can depend on any non-template
        # files, but not other templates since that could create a loop of changing hash
        # values.
        for k, v in by_name.items():
            if v.file_type.template:
                v.content = self._preprocess(k, v.content, self._hashes)

        # Add the preprocessed templates to the hash dictionary.
        self._hashes.update(
            (
                (k, sha256(v.content).digest())
                for k, v in by_name.items()
                if v.file_type.template
            ),
        )

        # Create the hash-to-asset dictionary.
        self._assets = {self._hashes[k]: v for k, v in by_name.items()}

        # Fill in the ETag header values.
        for k, v in self._assets.items():
            v.etag = b'"' + b64encode(k) + b'"'

        # If an assets directory was specified, extract them.
        if (extract_dir := environ.get("WEBPLAYERCONTROL_ASSETS")) is not None:
            extract_dir = Path(extract_dir)
            extract_dir.mkdir(parents=True, exist_ok=True)
            for k, v in self._assets.items():
                dest = extract_dir / urlsafe_b64encode(k).decode("ASCII")
                dest.write_bytes(v.content)
                v.extracted = dest

    @staticmethod
    def _preprocess(name: str, content: bytes, hashes: dict[str, bytes]) -> bytes:
        """
        Preprocess an asset file.

        Every occurrence of {{hash:<filename>}} will be replaced with the SHA256 of
        <filename>, encoded with base64. Every occurrence of {{hash-urlsafe:<filename>}}
        will be replaced with the SHA256 of <filename>, encoded with URL-safe base64.

        :param name: The name of this asset.
        :param content: The content to preprocess.
        :param hashes: The hashes of the assets.
        """
        for prefix, encode in (
            (b"{{hash:", b64encode),
            (b"{{hash-urlsafe:", urlsafe_b64encode),
        ):
            while (pos := content.find(prefix)) >= 0:
                end_pos = content.find(b"}}", pos)
                if end_pos < 0:
                    msg = f"Asset {name} contains {prefix} without matching b'}}'"
                    raise ValueError(msg)
                target_name = content[pos + len(prefix) : end_pos].decode("UTF-8")
                content = (
                    content[:pos] + encode(hashes[target_name]) + content[end_pos + 2 :]
                )
        return content

    async def maybe_handle_cas(
        self,
        path: str,
        scope: HTTPScope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> bool:
        """
        Handle a request if it matches the name of a content-addressable asset.

        :param path: The request path.
        :param scope: The scope.
        :param receive: The event-receive function.
        :param send: The event-send function.
        :return: True if the request matched a content-addressable asset and has been
            handled, or False if it did not match any such name.
        """
        try:
            h = urlsafe_b64decode(path.encode("UTF-8"))
        except binascii.Error:
            return False
        if (asset := self._assets.get(h)) is None:
            return False
        await self._handle(
            asset,
            b"max-age=31536000, immutable, stale-while-revalidate=31536000",
            scope,
            receive,
            send,
        )
        return True

    async def handle(
        self,
        asset: str,
        cache_control: bytes,
        scope: HTTPScope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:
        """
        Handle a request as a static asset.

        :param asset: The name of the asset to send.
        :param cache_control: The value of the cache-control header.
        :param scope: The scope.
        :param receive: The event-receive function.
        :param send: The event-send function.
        """
        await self._handle(
            self._assets[self._hashes[asset]],
            cache_control,
            scope,
            receive,
            send,
        )

    async def _handle(
        self,
        asset: _Asset,
        cache_control: bytes,
        scope: HTTPScope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:
        """
        Handle a request as a static asset.

        :param asset: Which asset to send.
        :param cache_control: The value of the cache-control header.
        :param scope: The scope.
        :param receive: The event-receive function.
        :param send: The event-send function.
        """
        # Receive and discard any request body.
        await discard_request(receive)

        # Static assets can only handle GET requests.
        if scope["method"] != "GET":
            await send_error(send, 405, "Method Not Allowed")
            return

        # Check that the asset’s content type is acceptable to the client.
        if not acceptable(asset.file_type.mime_type, scope["headers"]):
            await send_error(send, 406, "Not Acceptable")
            return

        # Prepare the headers.
        content_type = asset.file_type.mime_type
        if content_type.startswith(b"text/"):
            content_type += b"; charset=utf-8"
        headers = [
            (b"content-type", content_type),
            (b"content-length", str(len(asset.content)).encode("ASCII")),
            (b"cache-control", cache_control),
            (b"etag", asset.etag),
        ]

        # Check if the client is doing a conditional request that should be skipped.
        if unmodified(asset.etag, scope["headers"]):
            await send(
                {
                    "type": "http.response.start",
                    "status": 304,
                    "headers": headers,
                },
            )
            await send({"type": "http.response.body", "body": b""})
            return

        # Send the asset content.
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        if asset.extracted is not None:
            if "http.response.pathsend" in scope.get("extensions", {}):
                await send(
                    # asgi_types doesn’t know about pathsend
                    {  # pyright: ignore [reportArgumentType]
                        "type": "http.response.pathsend",
                        "path": str(asset.extracted),
                    },
                )
                return
            if not self._warned:
                getLogger(__name__).warning(
                    "WEBPLAYERCONTROL_ASSETS unused; server does not support "
                    "http.response.pathsend",
                )
                self._warned = True
        await send({"type": "http.response.body", "body": asset.content})
