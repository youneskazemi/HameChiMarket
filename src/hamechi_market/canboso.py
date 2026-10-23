from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

import httpx


BASE_URL = "https://canboso.com/api/v2/telegram-buyer"


class CanbosoError(RuntimeError):
    def __init__(self, code: str, status_code: int | None = None) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class CanbosoClient:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5)) as client:
                response = await client.request(
                    method,
                    f"{BASE_URL}/{path.lstrip('/')}",
                    params=params,
                    json=json_body,
                    headers=headers,
                )
                data = response.json()
        except (httpx.RequestError, ValueError) as exc:
            raise CanbosoError("UPSTREAM_UNAVAILABLE") from exc
        if not isinstance(data, dict):
            raise CanbosoError("INVALID_RESPONSE", response.status_code)
        if not response.is_success or data.get("success") is not True:
            code = data.get("code")
            raise CanbosoError(str(code or "UPSTREAM_REJECTED"), response.status_code)
        return data

    async def products(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "products", params={"key": self.api_key})
        products = data.get("products")
        if not isinstance(products, list):
            raise CanbosoError("INVALID_PRODUCTS_RESPONSE")
        return [item for item in products if isinstance(item, dict)]

    async def balance(self) -> tuple[str, Decimal]:
        data = await self._request("GET", "balance", params={"key": self.api_key})
        currency = str(data.get("walletCurrency", "")).upper()
        raw_balance = data.get("balanceUsd", data.get("balance"))
        try:
            amount = Decimal(str(raw_balance))
        except (InvalidOperation, TypeError) as exc:
            raise CanbosoError("INVALID_BALANCE_RESPONSE") from exc
        if not amount.is_finite() or amount < 0:
            raise CanbosoError("INVALID_BALANCE_RESPONSE")
        return currency, amount

    async def orders(self, order_code: str | None = None) -> list[dict[str, Any]]:
        data = await self._request(
            "GET",
            "orders",
            params={"orderCode": order_code} if order_code else None,
            headers={"x-buyer-key": self.api_key},
        )
        result = data.get("orders")
        if not isinstance(result, list):
            raise CanbosoError("INVALID_ORDERS_RESPONSE")
        return [item for item in result if isinstance(item, dict)]

    async def purchase(self, order_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = {"key": self.api_key, **payload}
        return await self._request(
            "POST",
            "purchase",
            json_body=body,
            headers={"Idempotency-Key": order_id},
        )
