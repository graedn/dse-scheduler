import pytest
from unittest.mock import patch, MagicMock
from teamup import TeamUpClient, TeamUpError


@pytest.fixture
def client():
    c = TeamUpClient(api_key="test-key", calendar_key="my-calendar")
    # Pre-seed subcalendar cache so create_event tests skip the subcalendars API call.
    # Two entries so keyword-based routing (proposed/accepted) can be tested.
    c._subcalendars_cache = [
        {"id": 1, "name": ".Proposed Broadcasts"},
        {"id": 2, "name": "Accepted Broadcasts"},
    ]
    return c


def mock_response(status: int, json_data: dict) -> MagicMock:
    resp = MagicMock()
    resp.ok = (status < 400)
    resp.status_code = status
    resp.json.return_value = json_data
    resp.text = str(json_data)
    return resp


def test_get_events_calls_correct_url(client):
    with patch.object(client.session, "get", return_value=mock_response(200, {"events": []})) as mock_get:
        result = client.get_events("2024-04-20", "2024-04-20")
        mock_get.assert_called_once()
        url = mock_get.call_args[0][0]
        assert "my-calendar/events" in url
        assert result == []


def test_get_events_returns_event_list(client):
    events = [{"id": "1", "title": "Test"}]
    with patch.object(client.session, "get", return_value=mock_response(200, {"events": events})):
        result = client.get_events("2024-04-20", "2024-04-20")
        assert result == events


def test_create_event_returns_id(client):
    with patch.object(client.session, "post", return_value=mock_response(200, {"event": {"id": "abc123"}})):
        event_id = client.create_event("[Premier] A vs B", 1713657600, 1713664800)
        assert event_id == "abc123"


def test_create_event_sends_correct_title(client):
    with patch.object(client.session, "post", return_value=mock_response(200, {"event": {"id": "1"}})) as mock_post:
        client.create_event("[Premier] A vs B", 1713657600, 1713664800)
        payload = mock_post.call_args[1]["json"]
        assert payload["title"] == "[Premier] A vs B"


def test_create_event_all_day_flag(client):
    with patch.object(client.session, "post", return_value=mock_response(200, {"event": {"id": "1"}})) as mock_post:
        client.create_event("🚫 NO STREAM", 1713657600, 1713744000, all_day=True)
        payload = mock_post.call_args[1]["json"]
        assert payload["all_day"] is True


def test_update_event_calls_put(client):
    with patch.object(client.session, "put", return_value=mock_response(200, {})) as mock_put:
        client.update_event("event-1", "[D1] C vs D", 1713657600, 1713664800)
        mock_put.assert_called_once()
        url = mock_put.call_args[0][0]
        assert "events/event-1" in url


def test_delete_event_calls_delete(client):
    with patch.object(client.session, "delete", return_value=mock_response(200, {})) as mock_del:
        client.delete_event("event-1")
        mock_del.assert_called_once()
        url = mock_del.call_args[0][0]
        assert "events/event-1" in url


def test_raises_teamup_error_on_bad_status(client):
    with patch.object(client.session, "get", return_value=mock_response(401, {"error": "Unauthorized"})):
        with pytest.raises(TeamUpError):
            client.get_events("2024-04-20", "2024-04-20")


def test_update_event_sends_correct_payload(client):
    with patch.object(client.session, "put", return_value=mock_response(200, {})) as mock_put:
        client.update_event("event-1", "[D1] C vs D", 1713657600, 1713664800)
        payload = mock_put.call_args[1]["json"]
        assert payload["title"] == "[D1] C vs D"
        assert "start_dt" in payload
        assert "end_dt" in payload


def test_create_event_includes_subcalendar_ids(client):
    with patch.object(client.session, "post", return_value=mock_response(200, {"event": {"id": "1"}})) as mock_post:
        client.create_event("[Premier] A vs B", 1713657600, 1713664800, subcalendar="proposed")
        payload = mock_post.call_args[1]["json"]
        assert payload["subcalendar_ids"] == [1]  # ".Proposed Broadcasts" id


def test_create_event_routes_to_accepted_subcalendar(client):
    with patch.object(client.session, "post", return_value=mock_response(200, {"event": {"id": "1"}})) as mock_post:
        client.create_event("[Premier] A vs B", 1713657600, 1713664800, subcalendar="accepted")
        payload = mock_post.call_args[1]["json"]
        assert payload["subcalendar_ids"] == [2]  # "Accepted Broadcasts" id


def test_create_event_bad_response_shape_raises(client):
    with patch.object(client.session, "post", return_value=mock_response(200, {"unexpected": "data"})):
        with pytest.raises(TeamUpError):
            client.create_event("[Premier] A vs B", 1713657600, 1713664800)


def test_update_event_includes_id_in_payload(client):
    """Regression: TeamUp PUT requires 'id' in the request body (400 without it)."""
    with patch.object(client.session, "put", return_value=mock_response(200, {})) as mock_put:
        client.update_event("event-42", "[D1] A vs B", 1713657600, 1713664800)
        payload = mock_put.call_args[1]["json"]
        assert payload["id"] == "event-42"


def test_update_event_routes_to_accepted_subcalendar(client):
    with patch.object(client.session, "put", return_value=mock_response(200, {})) as mock_put:
        client.update_event("event-1", "[D1] A vs B", 1713657600, 1713664800,
                            subcalendar="accepted")
        payload = mock_put.call_args[1]["json"]
        assert payload["subcalendar_ids"] == [2]  # "Accepted Broadcasts" id


def test_update_event_without_subcalendar_omits_subcalendar_ids(client):
    with patch.object(client.session, "put", return_value=mock_response(200, {})) as mock_put:
        client.update_event("event-1", "[D1] A vs B", 1713657600, 1713664800)
        payload = mock_put.call_args[1]["json"]
        assert "subcalendar_ids" not in payload
