Changes in 1.0.3
================

Bug fixes
---------

The command URLs for controlling a player previously allowed any HTTP method.
They now correctly only allow `POST` requests, returning 405 Method Not Allowed
for any other.

All HTTP responses may, and HTTP 405 Method Not Allowed responses *must*,
contain an `Allow` header indicating what HTTP methods are permitted to the
given URL. This was previously not sent. It now is.

All URLs that support the `GET` method should also support the `HEAD` method.
They previously did not, returning 405 Method Not Allowed instead. They now do.
Also, URLs that do not support `GET` now no longer send a body in their 405
Method Not Allowed responses if the method is `HEAD`.

All URLs should support the `OPTIONS` method. They previously did not,
returning 405 Method Not Allowed instead. They now do.

The page visibility API is now used to close the SSE connection when the page
is not visible and automatically reopen it when it becomes visible again. This
prevents errors on browsers which forcefully close the SSE connection when the
page is hidden, as well as saving resources due to not keeping the connection
open when it is not needed.

Changes in 1.0.2
================

Bug fixes
---------

The package no longer depends on `aioscgi`. It should work with any ASGI
container, and should not depend on one specific one.

Changes in 1.0.1
================

Bug fixes
---------

Tests no longer depend on `pytest-asyncio`. That plugin was never used and
should not have been declared as a dependency.
