class HelperError(Exception):
    """A refusal or failure, worded for a parent to read."""


class JobCancelled(HelperError):
    """The parent cancelled a task; what it had done was undone."""
