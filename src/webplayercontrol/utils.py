"""Utility functions used by multiple consumers."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from asgi_types import ASGIReceiveCallable, ASGISendCallable


def acceptable(mime_type: bytes, headers: Iterable[tuple[bytes, bytes]]) -> bool:
    """
    Check whether a given MIME type is acceptable to a client.

    :param mime_type: The MIME type the application is considering sending.
    :param headers: The request headers.
    :return: True if :param mime_type: is acceptable according to :param scope:’s Accept
        header, or False if not.
    """
    main_type = mime_type[: mime_type.find(b"/")]
    partial_wildcard = main_type + b"/*"
    wanted = {mime_type, partial_wildcard, b"*/*"}
    for name, value in headers:
        if name.lower() == b"accept":
            parts = value.split(b",")
            for part in parts:
                cleaned = part
                if (semi_pos := cleaned.find(b";")) >= 0:
                    cleaned = cleaned[:semi_pos]
                cleaned = cleaned.strip()
                if cleaned in wanted:
                    return True
            return False
    return True


async def discard_request(receive: ASGIReceiveCallable) -> None:
    """
    Read and discard the request body.

    :param receive: The receive callable.
    """
    more = True
    while more:
        event = await receive()
        event_type = event["type"]
        match event_type:
            case "http.disconnect":
                return
            case "http.request":
                more = event.get("more_body", False)
            case _:
                msg = f"Unexpected HTTP event {event_type}"
                raise ValueError(msg)


async def send_error(send: ASGISendCallable, status: int, message: str) -> None:
    """
    Send an HTTP error response.

    The caller must already have consumed all receive-body events.

    :param send: The event-send function.
    :param status: The HTTP status code.
    :param message: The message to display in the page.
    """
    body = f"""<!DOCTYPE HTML>
<html>
<head>
<title>{message}</title>
</head>
<body>
<h1>{message}</h1>
</body>
</html>
"""
    body = body.encode("UTF-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"text/html; charset=utf-8"),
                (b"content-length", str(len(body)).encode("UTF-8")),
            ],
        },
    )
    await send({"type": "http.response.body", "body": body})


def unmodified(etag: bytes, headers: Iterable[tuple[bytes, bytes]]) -> bool:
    """
    Check whether sending a resource can be skipped because the client already has it.

    This function must only be called for GET requests.

    :param etag: The ETag value of the resource, including the quotation marks.
    :param headers: The request headers.
    """
    for name, value in headers:
        if name.lower() == b"if-none-match":
            for i in value.split(b","):
                part = i.strip()
                if part == etag:
                    return True
                part = part.removeprefix(b"W/")
                if part == etag:
                    return True
    return False
