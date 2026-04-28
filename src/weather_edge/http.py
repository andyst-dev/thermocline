from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_HEADERS = {
    "User-Agent": "weather-edge/0.1",
    "Accept": "application/json",
}


def get_json(url: str, params: dict[str, Any] | None = None, timeout: int = 30) -> Any:
    final_url = url
    if params:
        final_url = f"{url}?{urlencode(params, doseq=True)}"
    req = Request(final_url, headers=DEFAULT_HEADERS)
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))
