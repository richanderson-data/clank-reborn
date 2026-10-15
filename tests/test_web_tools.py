"""Deterministic network boundaries plus explicit opt-in live service checks."""
import json
import os
import socket

import pytest

from clank.errors import ToolError
from clank.tools import build_default_registry
from clank.tools import web_tools as web
from clank.tools.permissions import Sandbox


@pytest.mark.parametrize("url", ["file:///C:/secret", "http://user:user@example.com", "http://127.0.0.1", "http://[::1]", "http://169.254.169.254", "http://10.0.0.1", "http://192.168.1.2", "http://example.com:8765", "http://["])
def test_rejects_non_public_targets(url):
    with pytest.raises(ToolError):
        web.public_target(url)


def test_rejects_private_dns_resolution(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))])
    with pytest.raises(ToolError, match="Private"):
        web.public_target("http://public-name.example")


def test_public_resolution_pins_exact_ip(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443))])
    assert web.public_target("https://example.com")[2] == "93.184.215.14"


def test_search_returns_source_urls_and_not_invented_results(monkeypatch):
    monkeypatch.setattr(web, "fetch", lambda url: ('<rss><channel><item><title>Real title</title><link>https://example.com/event</link><description>Dates need checking</description></item></channel></rss>', "application/rss+xml", url))
    result = json.loads(web.web_search("city events"))
    assert result["results"][0]["url"] == "https://example.com/event"
    assert "confirm event dates" in result["note"].lower()


def test_search_blocked_or_empty_is_not_a_fake_success(monkeypatch):
    monkeypatch.setattr(web, "fetch", lambda url: ("<html>blocked</html>", "text/html", url))
    with pytest.raises(ToolError, match="no results"):
        web.web_search("events")


def test_page_text_strips_scripts_and_has_source(monkeypatch):
    monkeypatch.setattr(web, "fetch", lambda url: ("<h1>Event</h1><script>evil()</script><style>hidden</style><p>October 13</p>", "text/html", url))
    result = web.read_webpage("https://example.com")
    assert "October 13" in result and "Source: https://example.com" in result
    assert "evil()" not in result and "hidden" not in result
    assert "not instructions" in result


def test_page_output_is_bounded(monkeypatch):
    monkeypatch.setattr(web, "fetch", lambda url: ("x" * 100000, "text/plain", url))
    assert len(web.read_webpage("https://example.com")) < 17000


def test_reader_exposes_calendar_links_to_follow(monkeypatch):
    monkeypatch.setattr(web, "fetch", lambda url: ('<p>Official guide</p><a href="/events/">Events calendar</a><a href="javascript:bad()">Bad</a>', "text/html", url))
    result = web.read_webpage("https://example.com/guide/")
    assert "Events calendar" in result and "https://example.com/events/" in result
    assert "javascript:bad()" not in result


def test_fetch_rejects_redirect_to_private_address(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 80))] if a[0] == "example.com" else [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))])
    class Response:
        status = 302
        def getheader(self, name):
            return "http://127.0.0.1/private"
    class Connection:
        def __init__(self, *args, **kwargs):
            pass
        def request(self, *args, **kwargs):
            pass
        def getresponse(self):
            return Response()
        def close(self):
            pass
    monkeypatch.setattr(socket, "create_connection", lambda *a, **kw: object())
    monkeypatch.setattr(web.http.client, "HTTPConnection", Connection)
    with pytest.raises(ToolError, match="Private"):
        web.fetch("http://example.com/")


def test_weather_uses_city_and_actual_units(monkeypatch):
    responses = [{"results": [{"name": "Tucson", "admin1": "Arizona", "country": "United States", "latitude": 32.22, "longitude": -110.92}]}, {"current": {"temperature_2m": 81, "time": "2026-10-13T12:00"}, "current_units": {"temperature_2m": "°F"}, "daily": {"time": ["2026-10-13", "2026-10-14"], "temperature_2m_max": [84, 76], "precipitation_probability_max": [24, 8]}}]
    urls = []

    def fetch(url):
        urls.append(url)
        return json.dumps(responses.pop(0)), "application/json", url

    monkeypatch.setattr(web, "fetch", fetch)
    result = json.loads(web.get_weather("Tucson", "US"))
    assert result["location"]["admin1"] == "Arizona"
    assert result["weather"]["current"]["temperature_2m"] == 81
    assert "temperature_unit=fahrenheit" in urls[1]
    assert "model estimates" in result["note"]
    assert result["weather"]["daily"][0] == {"date": "2026-10-13", "weekday": "Tuesday", "temperature_2m_max": 84, "precipitation_probability_max": 24}
    assert result["weather"]["daily"][1]["weekday"] == "Wednesday"


def test_weather_ambiguous_no_match_is_actionable(monkeypatch):
    monkeypatch.setattr(web, "fetch", lambda url: ("{}", "application/json", url))
    with pytest.raises(ToolError, match="No matching city"):
        web.get_weather("bad city")


def test_offline_registry_has_no_network_tools(tmp_path):
    sandbox = Sandbox([tmp_path])
    assert "web_search" not in build_default_registry(sandbox, web_enabled=False).names()
    assert {"web_search", "read_webpage", "get_weather"} <= set(build_default_registry(sandbox, web_enabled=True).names())


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("CLANK_TEST_WEB") != "1", reason="set CLANK_TEST_WEB=1 for public network requests")
def test_live_search_weather_and_page():
    weather = json.loads(web.get_weather("Tucson", "US"))
    assert weather["location"]["name"] == "Tucson"
    assert "temperature_2m" in weather["weather"]["current"]
    search = json.loads(web.web_search("Tucson Arizona events"))
    assert search["results"] and all(r["url"].startswith("http") for r in search["results"])
    assert "Example Domain" in web.read_webpage("https://example.com")
