import json
import unittest
from unittest.mock import patch

import httpx

from hamechi_market.canboso import BASE_URL, CanbosoClient, CanbosoError
from hamechi_market.payexa import PayexaClient


class FakeAsyncClient:
    def __init__(self, respond):
        self.respond = respond

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def request(self, method, url, *, params=None, json=None, headers=None):
        request = httpx.Request(method, url, params=params, json=json, headers=headers)
        return self.respond(request)

    async def post(self, url, *, json=None, headers=None):
        return await self.request("POST", url, json=json, headers=headers)


class ApiClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_canboso_requests_match_buyer_api_contract(self):
        requests = []

        def respond(request):
            requests.append(request)
            if request.url.path.endswith("/products"):
                body = {"success": True, "products": [{"productId": "prod-1"}, "invalid"]}
            elif request.url.path.endswith("/balance"):
                body = {"success": True, "walletCurrency": "USD", "balanceUsd": 12.5}
            elif request.url.path.endswith("/orders"):
                body = {"success": True, "orders": []}
            else:
                body = {"success": True, "order": {"orderCode": "SUP-1", "status": "completed"}}
            return httpx.Response(200, json=body, request=request)

        with patch("hamechi_market.canboso.httpx.AsyncClient", new=lambda *a, **kw: FakeAsyncClient(respond)):
            client = CanbosoClient("buyer-key")
            self.assertEqual(await client.products(), [{"productId": "prod-1"}])
            self.assertEqual(await client.balance(), ("USD", 12.5))
            self.assertEqual(await client.orders(), [])
            self.assertEqual(
                await client.purchase("HCM-ORDER-12345678", {"product_id": "prod-1", "quantity": 1}),
                {"success": True, "order": {"orderCode": "SUP-1", "status": "completed"}},
            )

        self.assertEqual(requests[0].url, f"{BASE_URL}/products?key=buyer-key")
        self.assertEqual(requests[1].url, f"{BASE_URL}/balance?key=buyer-key")
        self.assertEqual(requests[2].headers["x-buyer-key"], "buyer-key")
        self.assertEqual(requests[3].headers["Idempotency-Key"], "HCM-ORDER-12345678")
        self.assertEqual(
            json.loads(requests[3].content),
            {"key": "buyer-key", "product_id": "prod-1", "quantity": 1},
        )

    async def test_canboso_errors_are_reported_without_accepting_rejected_response(self):
        def respond(request):
            return httpx.Response(400, json={"success": False, "code": "INSUFFICIENT_BALANCE"}, request=request)

        with patch("hamechi_market.canboso.httpx.AsyncClient", new=lambda *a, **kw: FakeAsyncClient(respond)):
            with self.assertRaises(CanbosoError) as caught:
                await CanbosoClient("buyer-key").products()

        self.assertEqual(caught.exception.code, "INSUFFICIENT_BALANCE")
        self.assertEqual(caught.exception.status_code, 400)

    async def test_canboso_order_history_supports_exact_order_lookup(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(200, json={"success": True, "orders": [{"orderCode": "SUP-1"}]}, request=request)

        with patch("hamechi_market.canboso.httpx.AsyncClient", new=lambda *a, **kw: FakeAsyncClient(respond)):
            orders = await CanbosoClient("buyer-key").orders(order_code="SUP-1")

        self.assertEqual(orders, [{"orderCode": "SUP-1"}])
        self.assertEqual(requests[0].url.params["orderCode"], "SUP-1")
        self.assertEqual(requests[0].headers["x-buyer-key"], "buyer-key")

    async def test_payexa_create_and_verify_use_server_auth_and_matching_amount(self):
        requests = []

        def respond(request):
            requests.append(request)
            if request.url.path.endswith("/verify"):
                body = {"ok": True, "data": {"status": "paid", "amount": 100_000, "first_verification": True}}
            else:
                body = {"ok": True, "data": {"payment_id": "pay-1", "payment_url": "https://pay.test/pay-1"}}
            return httpx.Response(200, json=body, request=request)

        with patch("hamechi_market.payexa.httpx.AsyncClient", new=lambda *a, **kw: FakeAsyncClient(respond)):
            client = PayexaClient("payment-key", "https://pay.test/v2/payments")
            created = await client.create_payment(100_000, "HCM-1", "https://t.me/bot", "Order HCM-1")
            verified = await client.verify(created["payment_id"], 100_000)

        self.assertEqual(created, {"payment_id": "pay-1", "payment_url": "https://pay.test/pay-1"})
        self.assertEqual(verified["amount"], 100_000)
        self.assertEqual(requests[0].headers["Authorization"], "Bearer payment-key")
        self.assertEqual(json.loads(requests[0].content)["amount"], 100_000)
        self.assertEqual(requests[1].url.path, "/v2/payments/pay-1/verify")


if __name__ == "__main__":
    unittest.main()
