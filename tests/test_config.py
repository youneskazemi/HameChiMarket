import os
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

from hamechi_market.config import ConfigurationError, load_settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.dotenv = patch("hamechi_market.config.load_dotenv")
        self.dotenv.start()
        self.addCleanup(self.dotenv.stop)

    def test_required_bot_and_supplier_keys_fail_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ConfigurationError, "TELEGRAM_BOT_TOKEN"):
                load_settings()

        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "bot-token"}, clear=True):
            with self.assertRaisesRegex(ConfigurationError, "CANBOSO_API_KEY"):
                load_settings()

    def test_valid_settings_enable_checkout_only_with_all_required_values(self):
        env = {
            "TELEGRAM_BOT_TOKEN": "bot-token",
            "TELEGRAM_BOT_USERNAME": "@hamechi_bot",
            "CANBOSO_API_KEY": "buyer-key",
            "PAYEXA_API_KEY": "payment-key",
            "PAYEXA_MODE": "sandbox",
            "USD_TO_IRR_RATE": "1000000",
            "SELLER_MARGIN_BPS": "1500",
            "DELIVERY_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "ADMIN_TELEGRAM_IDS": "123, 456",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = load_settings()

        self.assertTrue(settings.checkout_ready)
        self.assertEqual(settings.telegram_username, "hamechi_bot")
        self.assertEqual(settings.admin_ids, frozenset({123, 456}))
        self.assertEqual(settings.payexa_base_url, "https://sandbox.pexn.ir/v2/payments")

    def test_invalid_pricing_and_encryption_settings_are_rejected(self):
        base = {
            "TELEGRAM_BOT_TOKEN": "bot-token",
            "CANBOSO_API_KEY": "buyer-key",
        }
        for rate in ("0", "NaN", "Infinity", "not-a-number"):
            with self.subTest(rate=rate), patch.dict(os.environ, {**base, "USD_TO_IRR_RATE": rate}, clear=True):
                with self.assertRaises(ConfigurationError):
                    load_settings()

        with patch.dict(os.environ, {**base, "DELIVERY_ENCRYPTION_KEY": "not-a-fernet-key"}, clear=True):
            with self.assertRaisesRegex(ConfigurationError, "DELIVERY_ENCRYPTION_KEY"):
                load_settings()

    def test_card_payment_settings_enable_checkout(self):
        env = {
            "TELEGRAM_BOT_TOKEN": "bot-token",
            "CANBOSO_API_KEY": "buyer-key",
            "PAYMENT_METHOD": "card",
            "CARD_NUMBER": "6037-9918-1234-5678",
            "CARD_HOLDER_NAME": "Test Holder",
            "USD_TO_IRR_RATE": "1000000",
            "SELLER_MARGIN_BPS": "1500",
            "DELIVERY_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "ADMIN_TELEGRAM_IDS": "123",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = load_settings()

        self.assertTrue(settings.checkout_ready)
        self.assertEqual(settings.payment_method, "card")
        self.assertEqual(settings.card_number, "6037991812345678")
        self.assertEqual(settings.card_holder_name, "Test Holder")

    def test_card_payment_missing_card_fails_checkout_ready(self):
        env = {
            "TELEGRAM_BOT_TOKEN": "bot-token",
            "CANBOSO_API_KEY": "buyer-key",
            "PAYMENT_METHOD": "card",
            "USD_TO_IRR_RATE": "1000000",
            "SELLER_MARGIN_BPS": "1500",
            "DELIVERY_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "ADMIN_TELEGRAM_IDS": "123",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = load_settings()

        self.assertFalse(settings.checkout_ready)

    def test_invalid_admin_id_does_not_enable_checkout(self):
        env = {
            "TELEGRAM_BOT_TOKEN": "bot-token",
            "CANBOSO_API_KEY": "buyer-key",
            "PAYEXA_API_KEY": "payment-key",
            "USD_TO_IRR_RATE": "1000000",
            "SELLER_MARGIN_BPS": "0",
            "DELIVERY_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "ADMIN_TELEGRAM_IDS": "not-an-id",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ConfigurationError, "ADMIN_TELEGRAM_IDS"):
                load_settings()


if __name__ == "__main__":
    unittest.main()
