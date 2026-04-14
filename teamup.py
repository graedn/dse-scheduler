import requests
from datetime import datetime, timezone

TEAMUP_BASE_URL = "https://api.teamup.com"


class TeamUpError(Exception):
    pass


class TeamUpClient:
    def __init__(self, api_key: str, calendar_key: str):
        self.api_key = api_key
        self.calendar_key = calendar_key
        self.session = requests.Session()
        self.session.headers.update({
            "Teamup-Token": api_key,
            "Content-Type": "application/json",
        })

    def _url(self, path: str) -> str:
        return f"{TEAMUP_BASE_URL}/{self.calendar_key}/{path}"

    def _check(self, resp: requests.Response):
        if not resp.ok:
            raise TeamUpError(f"TeamUp API error {resp.status_code}: {resp.text}")

    def get_events(self, start_date: str, end_date: str) -> list[dict]:
        """Fetch events between two dates (YYYY-MM-DD)."""
        resp = self.session.get(
            self._url("events"),
            params={"startDate": start_date, "endDate": end_date},
        )
        self._check(resp)
        return resp.json().get("events", [])

    def create_event(self, title: str, start_ts: int, end_ts: int,
                     all_day: bool = False) -> str:
        """Create an event. Returns the TeamUp event ID as a string."""
        start_dt = datetime.fromtimestamp(start_ts, tz=timezone.utc)
        end_dt = datetime.fromtimestamp(end_ts, tz=timezone.utc)
        payload = {
            "title": title,
            "start_dt": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_dt": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "all_day": all_day,
        }
        resp = self.session.post(self._url("events"), json=payload)
        self._check(resp)
        try:
            return str(resp.json()["event"]["id"])
        except (KeyError, ValueError) as exc:
            raise TeamUpError(f"Unexpected response shape from create_event: {resp.text}") from exc

    def update_event(self, event_id: str, title: str,
                     start_ts: int, end_ts: int) -> None:
        """Update an existing event's title and times. Raises TeamUpError on failure."""
        start_dt = datetime.fromtimestamp(start_ts, tz=timezone.utc)
        end_dt = datetime.fromtimestamp(end_ts, tz=timezone.utc)
        payload = {
            "title": title,
            "start_dt": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_dt": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        resp = self.session.put(self._url(f"events/{event_id}"), json=payload)
        self._check(resp)

    def delete_event(self, event_id: str) -> None:
        """Delete an event by ID. Raises TeamUpError on failure."""
        resp = self.session.delete(self._url(f"events/{event_id}"))
        self._check(resp)
