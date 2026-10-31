"""Unit tests for the utils module."""

import pytest

from webplayercontrol.utils import acceptable, unmodified


@pytest.mark.parametrize(
    argnames=("mime_type", "headers", "expected"),
    argvalues=[
        pytest.param(
            b"text/plain",
            [(b"host", b"myhostname")],
            True,
            id="no-accept-header",
        ),
        pytest.param(
            b"text/plain",
            [(b"accept", b"text/plain")],
            True,
            id="exact-match",
        ),
        pytest.param(
            b"text/plain",
            [(b"accept", b"text/html, text/plain, text/css")],
            True,
            id="exact-match-in-list",
        ),
        pytest.param(
            b"text/plain",
            [(b"accept", b"text/html, text/plain; charset=UTF-8; q=0.5, text/css")],
            True,
            id="ignores-parameters",
        ),
        pytest.param(
            b"text/plain",
            [(b"accept", b"text/html, text/*, application/json")],
            True,
            id="partial-wildcard",
        ),
        pytest.param(
            b"text/plain",
            [(b"accept", b"text/html, application/json, */*")],
            True,
            id="full-wildcard",
        ),
        pytest.param(
            b"text/plain",
            [(b"accept", b"text/html, text/css")],
            False,
            id="not-acceptable",
        ),
    ],
)
def test_acceptable(
    *,
    mime_type: bytes,
    headers: list[tuple[bytes, bytes]],
    expected: bool,
) -> None:
    """
    Check that the acceptable function returns the proper value.

    :param mime_type: The proposed MIME type of the resource.
    :param headers: The request headers.
    :param expected: The value that acceptable is expected to return.
    """
    actual = acceptable(mime_type, headers)
    assert actual == expected


@pytest.mark.parametrize(
    argnames=("etag", "headers", "expected"),
    argvalues=[
        pytest.param(b'"12345"', [], False, id="none"),
        pytest.param(b'"12345"', [(b"if-none-match", b'"12345"')], True, id="match"),
        pytest.param(
            b'"12345"',
            [(b"if-none-match", b'"123", "12345", "789"')],
            True,
            id="match-one-of",
        ),
        pytest.param(
            b'"12345"',
            [(b"if-none-match", b'W/"123", W/"12345", W/"789"')],
            True,
            id="match-one-of-weak",
        ),
        pytest.param(
            b'"12345"',
            [(b"if-none-match", b'"123", "456", "789"')],
            False,
            id="no-match",
        ),
        pytest.param(
            b'"12345"',
            [(b"if-none-match", b"*")],
            False,
            id="asterisk-not-relevant-for-get",
        ),
    ],
)
def test_unmodified(
    *,
    etag: bytes,
    headers: list[tuple[bytes, bytes]],
    expected: bool,
) -> None:
    """
    Check that the unmodified function returns the proper value.

    :param etag: The ETag of the resource.
    :param headers: The request headers.
    :param expected: The value that unmodified is expected to return.
    """
    actual = unmodified(etag, headers)
    assert actual == expected
