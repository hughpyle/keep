from __future__ import annotations

from types import SimpleNamespace

from keep.actions.assess_virustotal import AssessVirusTotal


def _response(status_code: int, payload: dict | None = None):
    class _Resp:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload or {}

        def json(self):
            return self._payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise AssertionError(f"unexpected HTTP error {self.status_code}")

    return _Resp(status_code, payload)


def test_assess_virustotal_marks_malicious_and_blocks(monkeypatch, tmp_path):
    monkeypatch.setenv("KEEP_CONFIG", str(tmp_path))
    monkeypatch.setenv("VIRUSTOTAL_API_KEY", "test-key")

    payload = {
        "data": {
            "attributes": {
                "last_analysis_stats": {"malicious": 2, "suspicious": 1},
                "last_analysis_results": {
                    "Engine A": {
                        "category": "malicious",
                        "engine_name": "Engine A",
                        "result": "phishing",
                    },
                    "Engine B": {
                        "category": "suspicious",
                        "engine_name": "Engine B",
                        "result": "suspicious",
                    },
                },
            }
        }
    }
    calls = []

    def _fake_get(url, headers, timeout):
        calls.append((url, headers["x-apikey"], timeout))
        return _response(200, payload)

    monkeypatch.setattr(
        "keep.actions.assess_virustotal.http_session",
        lambda: SimpleNamespace(get=_fake_get),
    )

    result = AssessVirusTotal().run(
        {
            "target_uri": "https://example.com",
            "uri": "https://example.com",
            "id": None,
            "tags": {"topic": "security"},
            "queue_background_tasks": True,
        },
        SimpleNamespace(),
    )

    assert result["assessment"] == "malicious"
    assert result["stop_processing"] is True
    assert result["skip_fetch"] is True
    assert result["uri"] is None
    assert result["id"] == "https://example.com"
    assert result["queue_background_tasks"] is False
    assert result["tags"]["assessment_virustotal"] == "malicious"
    assert "assessment_virustotal_checked_at" in result["tags"]
    assert "VirusTotal assessed https://example.com as malicious" in result["summary"]
    assert len(calls) == 1


def test_assess_virustotal_ok_uses_cache_without_tagging(monkeypatch, tmp_path):
    monkeypatch.setenv("KEEP_CONFIG", str(tmp_path))
    monkeypatch.setenv("VIRUSTOTAL_API_KEY", "test-key")

    payload = {
        "data": {
            "attributes": {
                "last_analysis_stats": {"malicious": 0, "suspicious": 0},
                "last_analysis_results": {},
            }
        }
    }
    calls = []

    def _fake_get(url, headers, timeout):
        calls.append((url, headers["x-apikey"], timeout))
        return _response(200, payload)

    monkeypatch.setattr(
        "keep.actions.assess_virustotal.http_session",
        lambda: SimpleNamespace(get=_fake_get),
    )

    params = {
        "target_uri": "https://example.com",
        "uri": "https://example.com",
        "id": "custom-id",
        "tags": {"topic": "security"},
        "queue_background_tasks": True,
    }

    first = AssessVirusTotal().run(dict(params), SimpleNamespace())
    second = AssessVirusTotal().run(dict(params), SimpleNamespace())

    assert first["assessment"] == "ok"
    assert first["uri"] == "https://example.com"
    assert first["id"] == "custom-id"
    assert first["stop_processing"] is False
    assert "assessment_virustotal" not in first["tags"]
    assert "assessment_virustotal_checked_at" not in first["tags"]
    assert second["assessment"] == "ok"
    assert "assessment_virustotal" not in second["tags"]
    assert len(calls) == 1


def test_assess_virustotal_is_disabled_without_api_key(monkeypatch, tmp_path):
    monkeypatch.setenv("KEEP_CONFIG", str(tmp_path))
    monkeypatch.delenv("VIRUSTOTAL_API_KEY", raising=False)
    monkeypatch.delenv("VT_API_KEY", raising=False)

    def _unexpected_get(*args, **kwargs):
        raise AssertionError("VirusTotal HTTP lookup should not run without an API key")

    monkeypatch.setattr(
        "keep.actions.assess_virustotal.http_session",
        lambda: SimpleNamespace(get=_unexpected_get),
    )

    result = AssessVirusTotal().run(
        {
            "target_uri": "https://example.com",
            "uri": "https://example.com",
            "id": "custom-id",
            "tags": {"topic": "security"},
            "queue_background_tasks": True,
        },
        SimpleNamespace(),
    )

    assert result["assessment"] == "disabled"
    assert result["uri"] == "https://example.com"
    assert result["id"] == "custom-id"
    assert result["stop_processing"] is False
    assert result["skip_fetch"] is False
    assert "assessment_virustotal" not in result["tags"]
