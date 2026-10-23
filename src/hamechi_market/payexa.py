from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx


class PayexaError(RuntimeError):
    def __init__(self, code: str, status_code: int | None = None) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class PayexaClient:
    def __init__(self, api_key: str, base_url: str) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    async def _post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5)) as client:
                response = await client.post(
                    f"{self.base_url}{path}",
                    json=body or {},
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                data = response.json()
        except (httpx.RequestError, ValueError) as exc:
            raise PayexaError("PAYMENT_PROVIDER_UNAVAILABLE") from exc
        if not isinstance(data, dict) or not response.is_success or data.get("ok") is not True:
            error = data.get("error") if isinstance(data, dict) else None
            code = error.get("code") if isinstance(error, dict) else None
            raise PayexaError(str(code or "PAYMENT_PROVIDER_REJECTED"), response.status_code)
        return data

    async def create_payment(
        self,
        amount_irr: int,
        order_id: str,
        return_url: str,
        description: str,
    ) -> dict[str, Any]:
        result = await self._post(
            "",
            {
                "amount": amount_irr,
                "order_id": order_id,
                "return_url": return_url,
                "description": description[:255],
            },
        )
        data = result.get("data")
        if not isinstance(data, dict) or not data.get("payment_id"):
            raise PayexaError("INVALID_PAYMENT_RESPONSE")
        payment_url = data.get("telegram_url") or data.get("payment_url")
        if not payment_url:
            raise PayexaError("PAYMENT_LINK_UNAVAILABLE")
        return {"payment_id": str(data["payment_id"]), "payment_url": str(payment_url)}

    async def verify(self, payment_id: str, amount_irr: int) -> dict[str, Any]:
        result = await self._post(
            f"/{quote(payment_id, safe='')}/verify",
            {"amount": amount_irr},
        )
        data = result.get("data")
        if not isinstance(data, dict):
            raise PayexaError("INVALID_VERIFICATION_RESPONSE")
        return data
