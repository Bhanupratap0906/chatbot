"""Item 3: the appointments router must hand the portal client a plain dict.

``portal_http_client.list_appointments`` is defined once (the validating
version); the router converts the validated DTO via ``to_payload()`` so a
DTO object is never JSON-serialized by httpx.
"""

from __future__ import annotations

from modules.portal.appointments_router import list_appointments


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def list_appointments(self, token: str, payload) -> dict:
        self.calls.append((token, payload))
        return {"ok": True}


async def test_router_sends_plain_dict_to_client() -> None:
    client = FakeClient()

    result = await list_appointments(
        page=2,
        perPage=10,
        startDate="2026-01-01",
        endDate="2026-01-31",
        search="john",
        status=["Confirmed"],
        token="tok",
        client=client,
    )

    assert result == {"ok": True}
    assert len(client.calls) == 1
    token, payload = client.calls[0]
    assert token == "tok"
    assert isinstance(payload, dict)
    # A plain dict, not a DTO instance carrying to_payload().
    assert not hasattr(payload, "to_payload")
    assert payload.get("page") == 2
    assert payload.get("perPage") == 10
    assert payload.get("search") == "john"
