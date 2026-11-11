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
