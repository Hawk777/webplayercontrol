What is MPRIS?
==============

The [Media Player Remote Interfacing
Specification](https://specifications.freedesktop.org/mpris/latest/), or MPRIS,
is a standard for remotely controlling media players via D-Bus in a Linux
system. Media players can expose MPRIS interfaces via D-Bus, and any
MPRIS-compliant client can control those players (pause, play, advance through
the playlist, adjust volume, seek, etc.).


What is webplayercontrol?
=========================

Webplayercontrol is a web application (implemented using
[ASGI](https://asgi.readthedocs.io/en/stable/) that provides a web interface
for controlling MPRIS-compliant media players. The web interface is optimized
for cell phone usage. The intention is to use a cell phone to control a media
player running on a desktop, for example while sitting too far away from the
desktop to use its own mouse and keyboard.


How do I set it up?
===================

You’ll need an ASGI server. There are lots available to choose from, some which
are usable on their own and some which expect to have a traditional Web server
(like Apache, Lighttpd, or Nginx) placed in front of them. Then, once
everything is installed, run your ASGI server and tell it the application
callable is `webplayercontrol:main`.


What options can I configure?
=============================

If your server supports [the pathsend
extension](https://asgi.readthedocs.io/en/stable/extensions.html#path-send),
you can set the `WEBPLAYERCONTROL_ASSETS` environment variable to the name of a
directory. The directory will be created, static assets will be extracted into
it, and the pathsend extension will be used to send those assets.
