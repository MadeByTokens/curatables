"""A stand-in for app.services.system_helper.SystemHelper in route tests."""

from app.services.system_helper import HelperFailed


class FakeHelper:
    """Answers each action from `responses` (a value, an exception to
    raise, or a function of the params) and records every call."""

    def __init__(self, **responses):
        self.available = True
        self.calls = []
        self.responses = {
            "account_status": {"user": "admin", "has_password": True, "ssh_enabled": False},
            "account_password_matches": {"matches": False},
            "wifi_status": {"wired": True, "wired_addresses": ["192.0.2.16"], "wifi_device": "wlan0",
                            "wifi_connected": False, "ssid": "", "signal": 0, "wifi_addresses": [],
                            "saved_ssid": "Home Net", "country": "GB"},
            "job_status": lambda p: {"id": p["id"], "title": "Working", "state": "running",
                                     "messages": ["step one"], "error": "", "result": None},
            **responses,
        }

    def call(self, action, **params):
        self.calls.append((action, params))
        if action not in self.responses:
            raise HelperFailed(f"unknown action '{action}'")
        answer = self.responses[action]
        if isinstance(answer, Exception):
            raise answer
        return answer(params) if callable(answer) else answer

    def job(self, job_id):
        return self.call("job_status", id=job_id)

    def linux_password_matches(self, password):
        return bool(self.call("account_password_matches", password=password)["matches"])

    def actions(self):
        return [a for a, _ in self.calls]

    def params(self, action):
        return next(p for a, p in self.calls if a == action)


def job(job_id="job123", title="Working"):
    return {"id": job_id, "title": title, "state": "running", "messages": [], "error": "", "result": None}
