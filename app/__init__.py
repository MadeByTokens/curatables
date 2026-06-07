"""Curatables — local video curation server for parents and kids."""

# Single source of truth for the running version. Keep in sync with
# pyproject.toml's [project] version field; the /healthz endpoint
# reports this value so operators can confirm what their instance is
# running.
__version__ = "0.5.0"
