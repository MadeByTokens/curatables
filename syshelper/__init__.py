"""The Curatables system helper: the one root process the web app talks to.

The web server runs as an unprivileged, sandboxed user and cannot change
passwords, Wi-Fi, drives or services. This helper runs as root, listens
on a Unix socket only the service user may use, and does a short, fixed
list of things (the actions registered in this package). Nothing it
accepts is ever run as a shell command: every parameter is validated and
passed as a separate argument.

Standard library only, so it runs on the system Python with no venv.
"""
