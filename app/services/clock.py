"""Set the box's clock from a visitor's device when NTP has not.

The Pi has no battery-backed clock. After a power cut it starts from the
time fake-hwclock last saved and stays behind until NTP reaches the
internet, which may be never on a home network without internet. Until
then pages ask the browser for its time (static/js/clock.js) and the
root helper steps the clock forward.

Any signed-in kid's or parent's device may correct the clock. Cache
eviction, which deletes files by how long ago they were watched, only
trusts a clock that NTP or a parent's device set.
"""

from __future__ import annotations

import logging
import threading
import time

from app.services.system_helper import HelperFailed, HelperUnavailable, SystemHelper

logger = logging.getLogger(__name__)

# How far apart the two clocks may be before the box's is changed.
CLOSE_ENOUGH_SECONDS = 120
CHECK_EVERY_SECONDS = 60.0
OFFER_EVERY_SECONDS = 10.0


class ClockService:
    def __init__(self, helper: SystemHelper):
        self.helper = helper
        self._lock = threading.Lock()
        self._ntp = False            # NTP has synchronized the clock
        self._from_device = False    # a visitor's device set or confirmed it
        self._from_parent = False    # ... and that device was a parent's
        self._checked = float("-inf")
        self._offered = {False: float("-inf"), True: float("-inf")}  # by kid, by parent

    def _ntp_synced(self) -> bool | None:
        """None when there is no helper to ask (not the box)."""
        if not self.helper.available:
            return None
        with self._lock:
            if self._ntp:
                return True
            now = time.monotonic()
            if now - self._checked >= CHECK_EVERY_SECONDS:
                self._checked = now
                try:
                    self._ntp = bool(self.helper.call("clock_status")["synchronized"])
                except (HelperFailed, HelperUnavailable, KeyError, TypeError):
                    pass
            return self._ntp

    def wants_browser_time(self, parent: bool = False) -> bool:
        """Whether a page should send the device's time: while nothing has
        set the clock, and on parent pages until a parent's device has."""
        ntp = self._ntp_synced()
        if ntp is None or ntp:
            return False
        return not (self._from_parent if parent else self._from_device)

    def trusted(self) -> bool:
        """Whether ages measured from now (cache eviction) can be relied on."""
        ntp = self._ntp_synced()
        return ntp is None or ntp or self._from_parent

    def offer(self, browser_epoch: float, by_parent: bool = False) -> str:
        """A signed-in visitor's current time, in seconds. Returns what was done."""
        if not self.wants_browser_time(parent=by_parent):
            return "not needed"
        with self._lock:
            now = time.monotonic()
            if now - self._offered[by_parent] < OFFER_EVERY_SECONDS:
                return "too soon"
            self._offered[by_parent] = now
        step = browser_epoch - time.time()
        if step < -CLOSE_ENOUGH_SECONDS:
            return "behind"
        if step > CLOSE_ENOUGH_SECONDS:
            try:
                result = self.helper.call("set_clock", epoch=int(browser_epoch))
            except (HelperFailed, HelperUnavailable) as e:
                logger.warning("Could not set the clock from a browser: %s", e)
                return "refused"
            if result.get("reason") == "synchronized":
                with self._lock:
                    self._ntp = True
                return "not needed"
            if not result.get("set"):
                return "refused"
            logger.info("Clock moved forward %s s to a %s's device", result.get("step"),
                        "parent" if by_parent else "kid")
        with self._lock:
            self._from_device = True
            self._from_parent = self._from_parent or by_parent
        return "set" if step > CLOSE_ENOUGH_SECONDS else "close enough"
