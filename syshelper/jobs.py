"""Background tasks (formatting a drive, switching Wi-Fi, ...).

Only one runs at a time: two tasks touching drives or the network at
once could undo each other.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from syshelper.errors import HelperError, JobCancelled

logger = logging.getLogger("curatables-helper")
KEEP = 20


class Job:
    def __init__(self, action: str, title: str):
        self.id = uuid.uuid4().hex[:12]
        self.action = action
        self.title = title
        self.state = "running"
        self.messages: list[str] = []
        self.result: Any = None
        self.error = ""
        self.started = time.time()
        self.finished: float | None = None
        # A step that can be stopped cleanly (a copy) sets cancellable;
        # progress is a one-line "how far" for the page.
        self.cancellable = False
        self.progress = ""
        self._cancel = threading.Event()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def request_cancel(self) -> bool:
        if self.state != "running" or not self.cancellable:
            return False
        self._cancel.set()
        return True

    def log(self, message: str) -> None:
        logger.info("%s: %s", self.action, message)
        self.messages.append(message)
        del self.messages[:-200]

    def as_dict(self) -> dict:
        return {"id": self.id, "action": self.action, "title": self.title, "state": self.state,
                "messages": list(self.messages), "result": self.result, "error": self.error,
                "started": self.started, "finished": self.finished,
                "cancellable": self.cancellable and not self.cancel_requested,
                "cancelling": self.cancel_requested and self.state == "running",
                "progress": self.progress}


class Jobs:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}

    def running(self) -> Job | None:
        return next((j for j in self._jobs.values() if j.state == "running"), None)

    def start(self, action: str, title: str, fn: Callable[[Job], Any]) -> Job:
        with self._lock:
            busy = self.running()
            if busy:
                raise HelperError(f"Another task is still running ({busy.title}). "
                                  "Wait for it to finish and try again.")
            job = Job(action, title)
            self._jobs[job.id] = job
            for old in sorted(self._jobs.values(), key=lambda j: j.started)[:-KEEP]:
                if old.state != "running":
                    del self._jobs[old.id]
        threading.Thread(target=self._run, args=(job, fn), daemon=True).start()
        return job

    def _run(self, job: Job, fn: Callable[[Job], Any]) -> None:
        try:
            job.result = fn(job)
            job.state = "done"
        except JobCancelled as e:
            job.error = str(e)
            job.state = "cancelled"
        except HelperError as e:
            job.error = str(e)
            job.state = "failed"
        except Exception as e:
            logger.exception("%s crashed", job.action)
            job.error = f"Something unexpected went wrong ({type(e).__name__}). The diagnostics report has details."
            job.state = "failed"
        finally:
            job.cancellable = False
            job.progress = ""
            job.finished = time.time()

    def get(self, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise HelperError("That task is not known (the helper may have restarted).")
        return job

    def latest(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.started, reverse=True)


JOBS = Jobs()
