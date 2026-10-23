import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from cryptography.fernet import Fernet

from hamechi_market.bot import build_application
from hamechi_market.config import Settings


class ApplicationWiringTests(unittest.TestCase):
    def test_application_registers_all_user_routes_without_starting_networking(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(
                telegram_token="123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
                telegram_username="hamechi_bot",
                canboso_key="buyer-key",
                payexa_key="payment-key",
                payexa_mode="sandbox",
                payexa_base_url="https://sandbox.pexn.ir/v2/payments",
                usd_to_irr_rate=Decimal("1000000"),
                seller_margin_bps=1000,
                encryption_key=Fernet.generate_key(),
                database_path=Path(temp) / "orders.sqlite3",
                admin_ids=frozenset({123}),
            )

            application = build_application(settings)

            self.assertEqual(len(application.handlers[0]), 8)
            self.assertIs(application.bot_data["settings"], settings)
            self.assertIsNotNone(application.bot_data["store"])
            self.assertIsNotNone(application.bot_data["canboso"])
            self.assertIsNotNone(application.bot_data["payexa"])


if __name__ == "__main__":
    unittest.main()
