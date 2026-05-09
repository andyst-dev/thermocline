from __future__ import annotations

from urllib.error import URLError

import pytest

from weather_edge.clients import nasa_gistemp


GISTEMP_SAMPLE = """
Year Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec J-D D-N DJF MAM JJA SON
2000 10 20 30 40 50 60 70 80 90 100 110 120 65 **** **** **** **** ****
2001 20 30 40 50 60 70 80 90 100 110 120 130 75 **** **** **** **** ****
"""


def _reset_unavailable_reason(monkeypatch):
    monkeypatch.setattr(nasa_gistemp, "_GISTEMP_UNAVAILABLE_REASON", None)


def test_global_temp_baseline_uses_cached_table_when_nasa_times_out(tmp_path, monkeypatch):
    _reset_unavailable_reason(monkeypatch)
    cache_path = tmp_path / "gistemp.txt"
    cache_path.write_text(GISTEMP_SAMPLE)
    monkeypatch.setattr(nasa_gistemp, "GISTEMP_CACHE_PATH", cache_path)

    def fail_fetch() -> str:
        raise URLError("timed out")

    monkeypatch.setattr(nasa_gistemp, "_fetch_table", fail_fetch)

    baseline = nasa_gistemp.global_temp_baseline(month=5, lookback_years=2)

    assert baseline.mean_c == 0.55
    assert baseline.samples == [0.5, 0.6]
    assert baseline.source_url.endswith("GLB.Ts+dSST.txt")


def test_global_temp_baseline_refreshes_cache_after_successful_fetch(tmp_path, monkeypatch):
    _reset_unavailable_reason(monkeypatch)
    cache_path = tmp_path / "gistemp.txt"
    monkeypatch.setattr(nasa_gistemp, "GISTEMP_CACHE_PATH", cache_path)
    monkeypatch.setattr(nasa_gistemp, "_fetch_table", lambda: GISTEMP_SAMPLE)

    baseline = nasa_gistemp.global_temp_baseline(month=1, lookback_years=2)

    assert baseline.samples == [0.1, 0.2]
    assert cache_path.read_text() == GISTEMP_SAMPLE


def test_global_temp_baseline_memoizes_unavailable_nasa_without_cache(tmp_path, monkeypatch):
    _reset_unavailable_reason(monkeypatch)
    cache_path = tmp_path / "missing-gistemp.txt"
    monkeypatch.setattr(nasa_gistemp, "GISTEMP_CACHE_PATH", cache_path)
    calls = {"count": 0}

    def fail_fetch() -> str:
        calls["count"] += 1
        raise URLError("timed out")

    monkeypatch.setattr(nasa_gistemp, "_fetch_table", fail_fetch)

    with pytest.raises(URLError):
        nasa_gistemp.global_temp_baseline(month=1)
    with pytest.raises(RuntimeError, match="timed out"):
        nasa_gistemp.global_temp_baseline(month=2)

    assert calls["count"] == 1
