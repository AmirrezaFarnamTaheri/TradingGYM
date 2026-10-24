"""Stream bounded provider envelopes before JSON parsing or buffering."""

from typing import Any
import json as json_codec
import httpx
from trading_gym.intelligence.deadlines import check_deadline, remaining_seconds
from trading_gym.intelligence.types import InvalidDecisionSignal


def strict_response_json(response: httpx.Response) -> Any:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InvalidDecisionSignal("duplicate_provider_json_key")
            result[key] = value
        return result

    def nonfinite(_):
        raise InvalidDecisionSignal("nonfinite_provider_json")

    try:
        return json_codec.loads(
            response.content, object_pairs_hook=unique, parse_constant=nonfinite
        )
    except (ValueError, UnicodeError) as exc:
        raise InvalidDecisionSignal("malformed_provider_json") from exc


def bounded_post(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
    json: Any,
    timeout: float,
    max_bytes: int = 256 * 1024
) -> httpx.Response:
    if type(max_bytes) is not int or max_bytes < 1:  # noqa: E721
        raise ValueError("positive response byte limit required")
    check_deadline()
    # Refuse compression rather than allowing a small compressed envelope to
    # expand unboundedly in a decoder before the application can count bytes.
    headers = {**headers, "Accept-Encoding": "identity"}
    with client.stream(
        "POST", url, headers=headers, json=json, timeout=remaining_seconds(timeout)
    ) as response:
        if response.headers.get("content-encoding", "identity").lower() not in (
            "",
            "identity",
        ):
            raise InvalidDecisionSignal("provider_compressed_response_denied")
        content = bytearray()
        for chunk in response.iter_raw():
            check_deadline()
            if len(content) + len(chunk) > max_bytes:
                raise InvalidDecisionSignal("provider_response_byte_budget_exceeded")
            content.extend(chunk)
        check_deadline()
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            content=bytes(content),
            request=response.request,
        )
