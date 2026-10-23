import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

from hamechi_market.storage import OrderStore


class OrderStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = OrderStore(Path(self.temp.name) / "orders.sqlite3", Fernet.generate_key())

    def create_order(self):
        self.store.create_order(
            {
                "order_id": "HCM-1234567890ABCDEF12345678",
                "user_id": 100,
                "chat_id": 100,
                "product_id": "prod-1",
                "product_name": "Test account",
                "product_type": "account",
                "quantity": 1,
                "amount_irr": 100_000,
                "supplier_price_usd": "1.25",
            },
            {"product_id": "prod-1", "customer_email": "buyer@example.com"},
        )

    def test_order_inputs_are_encrypted_and_scoped_to_owner(self):
        self.create_order()
        row = self.store.get_by_id("HCM-1234567890ABCDEF12345678")

        self.assertNotIn(b"buyer@example.com", row["encrypted_request"])
        self.assertEqual(
            self.store.purchase_request("HCM-1234567890ABCDEF12345678"),
            {"product_id": "prod-1", "customer_email": "buyer@example.com"},
        )
        self.assertIsNone(self.store.get_by_id("HCM-1234567890ABCDEF12345678", user_id=999))

    def test_payment_and_supplier_transitions_are_idempotent(self):
        self.create_order()
        order_id = "HCM-1234567890ABCDEF12345678"

        self.store.attach_payment(order_id, "payment-1")
        self.assertTrue(self.store.mark_paid(order_id))
        self.assertFalse(self.store.mark_paid(order_id))
        self.assertTrue(self.store.begin_supplier_purchase(order_id))
        self.assertTrue(self.store.begin_supplier_purchase(order_id))
        self.assertFalse(self.store.update_supplier_status(order_id, "completed", "fulfilled"))

        self.store.save_purchase_result(
            order_id,
            "fulfilled",
            "supplier-1",
            "completed",
            {"accounts": [{"user": "buyer@example.com", "password": "secret"}]},
        )

    def test_get_active_unpaid_order_and_mark_paid_from_review(self):
        self.create_order()
        order_id = "HCM-1234567890ABCDEF12345678"
        active = self.store.get_active_unpaid_order(100)
        self.assertIsNotNone(active)
        self.assertEqual(active["order_id"], order_id)

        self.store.update_status(order_id, "payment_review")
        reviewing = self.store.get_active_unpaid_order(100)
        self.assertIsNotNone(reviewing)
        self.assertEqual(reviewing["status"], "payment_review")

        # mark_paid should succeed from payment_review
        self.assertTrue(self.store.mark_paid(order_id))
        self.assertIsNone(self.store.get_active_unpaid_order(100))
        self.assertEqual(self.store.get_by_id(order_id)["status"], "paid")


if __name__ == "__main__":
    unittest.main()
