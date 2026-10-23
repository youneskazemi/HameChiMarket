import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from cryptography.fernet import Fernet

from telegram.constants import ChatType

from hamechi_market.bot import (
    _begin_checkout,
    _show_catalog,
    _show_catalog_entry,
    _show_product,
    _collect_requirement,
    _main_keyboard,
    _process_verified_order,
    _quote_irr,
    _show_orders,
    cancel_command,
    callback,
    receipt_handler,
    start,
    text_router,
)
from hamechi_market.config import Settings
from hamechi_market.storage import OrderStore


PRODUCT = {
    "productId": "prod-1",
    "name": "Test account",
    "productType": "account",
    "price": {"amount": 1.25, "currency": "USD"},
    "availability": {"available": 5},
    "purchaseRequirements": {},
}


class FakeMessage:
    def __init__(self, text=None, photo=None, document=None):
        self.text = text
        self.photo = photo
        self.document = document
        self.caption = None
        self.sent = []

    async def reply_text(self, text, reply_markup=None, *args, **kwargs):
        self.sent.append((text, reply_markup))

    async def edit_message_text(self, text, reply_markup=None, *args, **kwargs):
        self.sent.append((text, reply_markup))

    async def edit_message_caption(self, caption, reply_markup=None, *args, **kwargs):
        self.sent.append((caption, reply_markup))


class FakeCanboso:
    def __init__(self, product=PRODUCT, purchase_result=None):
        self.product = product
        self.purchase_result = purchase_result or {
            "order": {"orderCode": "SUP-1", "status": "completed", "productType": "account"},
            "delivery": {"accounts": [{"user": "buyer@example.com", "password": "secret"}]},
        }
        self.purchase_calls = []
        self.balance_calls = 0
        self.balance_value = Decimal("10")
        self.order_history = {}
        self.order_queries = []

    async def products(self):
        return [self.product]

    async def balance(self):
        self.balance_calls += 1
        return "USD", self.balance_value

    async def purchase(self, order_id, payload):
        self.purchase_calls.append((order_id, payload))
        return self.purchase_result

    async def orders(self, order_code=None):
        self.order_queries.append(order_code)
        return self.order_history.get(order_code, []) if order_code else []


class FakePayexa:
    def __init__(self, first_verification=True, amount=None):
        self.first_verification = first_verification
        self.amount = amount
        self.verify_calls = []

    async def create_payment(self, amount_irr, order_id, return_url, description):
        return {"payment_id": f"pay-{order_id}", "payment_url": "https://pay.test/checkout"}

    async def verify(self, payment_id, amount_irr):
        self.verify_calls.append((payment_id, amount_irr))
        order_id = payment_id.removeprefix("pay-")
        return {
            "status": "paid",
            "order_id": order_id,
            "payment_id": payment_id,
            "amount": self.amount if self.amount is not None else amount_irr,
            "first_verification": self.first_verification,
        }


class BotFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(
            telegram_token="bot-token",
            telegram_username="hamechi_bot",
            canboso_key="buyer-key",
            payexa_key="payment-key",
            payexa_mode="sandbox",
            payexa_base_url="https://sandbox.pexn.ir/v2/payments",
            usd_to_irr_rate=Decimal("1000000"),
            seller_margin_bps=1500,
            encryption_key=Fernet.generate_key(),
            database_path=Path(self.temp.name) / "orders.sqlite3",
            admin_ids=frozenset({123}),
        )
        self.store = OrderStore(self.settings.database_path, self.settings.encryption_key)
        self.canboso = FakeCanboso()
        self.payexa = FakePayexa()
        self.message = FakeMessage()
        self.admin_photos = []
        self.context = SimpleNamespace(
            application=SimpleNamespace(
                bot_data={
                    "settings": self.settings,
                    "store": self.store,
                    "canboso": self.canboso,
                    "payexa": self.payexa,
                    "bot_username": "hamechi_bot",
                }
            ),
            user_data={},
            args=[],
            bot=SimpleNamespace(
                send_message=self.send_admin_message,
                send_photo=self.send_admin_photo,
            ),
        )
        self.admin_messages = []
        self.user = SimpleNamespace(id=100, username="testbuyer")
        self.chat = SimpleNamespace(id=100, type=ChatType.PRIVATE)
        self.query = SimpleNamespace(message=self.message, data=None)

        async def answer(text=None, show_alert=False):
            return None

        self.query.answer = answer

        async def edit_message_text(text, reply_markup=None, *args, **kwargs):
            self.message.sent.append((text, reply_markup))

        async def edit_message_caption(caption, reply_markup=None, *args, **kwargs):
            self.message.sent.append((caption, reply_markup))

        self.query.edit_message_text = edit_message_text
        self.query.edit_message_caption = edit_message_caption
        self.update = SimpleNamespace(
            effective_user=self.user,
            effective_chat=self.chat,
            effective_message=self.message,
            callback_query=self.query,
        )

    async def send_admin_photo(self, chat_id, photo, caption=None, reply_markup=None, parse_mode=None):
        self.admin_photos.append((chat_id, photo, caption, reply_markup))

    async def send_admin_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        self.admin_messages.append((chat_id, text, reply_markup))

    def test_main_menu_highlights_the_primary_shop_action(self):
        keyboard = _main_keyboard()

        self.assertEqual(keyboard.inline_keyboard[0][0].style, "primary")

    def test_main_keyboard_has_support_action(self):
        keyboard = _main_keyboard()

        support = [button for row in keyboard.inline_keyboard for button in row if button.text == "💬 پشتیبانی"]
        self.assertEqual(len(support), 1)
        self.assertEqual(support[0].url, "https://t.me/EZPY9798")

    async def test_start_shows_a_persistent_native_menu(self):
        self.update.callback_query = None

        await start(self.update, self.context)

        keyboard = self.message.sent[-1][1]
        labels = [button.text for row in keyboard.keyboard for button in row]
        self.assertTrue(keyboard.is_persistent)
        self.assertIn("🛍️ محصولات", labels)
        self.assertIn("📦 سفارش‌ها", labels)
        self.assertIn("🔎 جست‌وجو", labels)
        self.assertIn("💬 پشتیبانی", labels)

    async def test_support_menu_shows_direct_contact_link(self):
        self.message.text = "💬 پشتیبانی"
        await text_router(self.update, self.context)
        markup = self.message.sent[-1][1]
        support = [button for row in markup.inline_keyboard for button in row]
        self.assertEqual(support[0].url, "https://t.me/EZPY9798")
        self.assertFalse(self.context.user_data.get("awaiting_support_message"))
        self.assertEqual(self.admin_messages, [])

    async def test_products_menu_groups_available_items_by_provider_brand(self):
        products = [
            {**PRODUCT, "productId": "canva-1", "name": "Canva Pro", "emoji": "canva"},
            {**PRODUCT, "productId": "canva-2", "name": "Canva Education", "emoji": "canva"},
            {**PRODUCT, "productId": "office", "name": "Microsoft 365", "emoji": "microsoft_office_365"},
            {**PRODUCT, "productId": "other", "name": "Other item", "emoji": "none"},
        ]

        async def list_products():
            return products

        self.canboso.products = list_products
        self.message.text = "🛍️ محصولات"

        await text_router(self.update, self.context)

        markup = self.message.sent[-1][1]
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("list:brand-canva:0", callbacks)
        self.assertIn("list:brand-microsoft_office_365:0", callbacks)
        self.assertIn("list:brand-other:0", callbacks)
        self.assertNotIn("list:account:0", callbacks)
        self.assertIn("search:start", callbacks)

    async def test_brand_category_shows_only_matching_provider_brand(self):
        products = [
            {**PRODUCT, "productId": "claude", "name": "Claude Pro", "emoji": "claude"},
            {**PRODUCT, "productId": "canva", "name": "Canva Pro", "emoji": "canva"},
        ]

        async def list_products():
            return products

        self.canboso.products = list_products

        await _show_catalog(self.update, self.context, "brand-claude", 0)

        markup = self.message.sent[-1][1]
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("prod:claude", callbacks)
        self.assertNotIn("prod:canva", callbacks)

    async def test_catalog_search_matches_provider_description(self):
        products = [
            {
                **PRODUCT,
                "productId": "chatgpt",
                "name": "ChatGPT Plus",
                "description": "اشتراک ماهانهٔ هوش مصنوعی",
            },
            {**PRODUCT, "productId": "canva", "name": "Canva Pro", "description": "طراحی آنلاین"},
        ]

        async def list_products():
            return products

        self.canboso.products = list_products
        self.context.user_data["product_search_query"] = "هوش مصنوعي"

        await _show_catalog(self.update, self.context, "search", 0)

        markup = self.message.sent[-1][1]
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("prod:chatgpt", callbacks)
        self.assertNotIn("prod:canva", callbacks)

    async def test_all_brand_categories_appear_without_category_pagination(self):
        brands = "abcdefghijklm"
        products = [
            {**PRODUCT, "productId": f"brand-{brand}", "name": f"Brand {brand}", "emoji": f"brand_{brand}"}
            for brand in brands
        ]

        async def list_products():
            return products

        self.canboso.products = list_products
        await _show_catalog_entry(self.update, self.context)

        markup = self.message.sent[-1][1]
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("list:brand-brand_a:0", callbacks)
        self.assertIn("list:brand-brand_m:0", callbacks)
        self.assertFalse(any(value.startswith("categories:") for value in callbacks))
        self.assertNotIn("صفحهٔ", self.message.sent[-1][0])

    async def test_product_search_shows_only_matching_results(self):
        products = [
            {**PRODUCT, "productId": "chatgpt", "name": "ChatGPT Plus"},
            {**PRODUCT, "productId": "canva", "name": "Canva Pro"},
        ]

        async def list_products():
            return products

        self.canboso.products = list_products
        self.query.data = "search:start"
        await callback(self.update, self.context)
        self.update.callback_query = None
        self.message.text = "chatgpt"

        await text_router(self.update, self.context)

        markup = self.message.sent[-1][1]
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("prod:chatgpt", callbacks)
        self.assertNotIn("prod:canva", callbacks)
        self.assertEqual(self.context.user_data["catalog_location"], ("search", 0))

    async def test_product_details_use_provider_name_and_description_verbatim(self):
        self.canboso.product = {
            **PRODUCT,
            "name": "اشتراک فارسی کانبوسو",
            "description": "توضیح رسمی محصول به فارسی",
        }

        await _show_product(self.update, self.context, "prod-1")

        self.assertIn("اشتراک فارسی کانبوسو", self.message.sent[-1][0])
        self.assertIn("توضیح رسمی محصول به فارسی", self.message.sent[-1][0])

    async def test_cancel_button_clears_an_unpaid_checkout_draft(self):
        self.context.user_data["draft"] = {"expected": "email"}
        self.query.data = "cancel"

        await callback(self.update, self.context)

        self.assertNotIn("draft", self.context.user_data)
        self.assertEqual(self.store.list_for_user(100), [])
        self.assertIn("لغو", self.message.sent[-1][0])
        self.assertEqual(self.message.sent[-1][1].inline_keyboard[0][0].style, "primary")

    async def test_cancel_command_clears_an_unpaid_checkout_draft(self):
        self.context.user_data["draft"] = {"expected": "email"}
        self.update.callback_query = None

        await cancel_command(self.update, self.context)

        self.assertNotIn("draft", self.context.user_data)
        self.assertIn("لغو", self.message.sent[-1][0])

    async def test_product_back_button_restores_category_and_page(self):
        products = [
            {**PRODUCT, "productId": f"slot-{index}", "productType": "slot"}
            for index in range(7)
        ]

        async def list_products():
            return products

        self.canboso.products = list_products
        await _show_catalog(self.update, self.context, "slot", 1)
        await _show_product(self.update, self.context, "slot-5")

        back_button = self.message.sent[-1][1].inline_keyboard[-1][0]
        self.assertEqual(back_button.callback_data, "list:slot:1")

    def test_price_rounds_up_to_whole_thousand_riyal(self):
        self.assertEqual(_quote_irr(Decimal("1.25"), self.settings), 1_438_000)

    async def test_sandbox_checkout_and_verification_never_spend_supplier_wallet(self):
        self.canboso.balance_value = Decimal("0")

        await _begin_checkout(self.update, self.context, "prod-1")
        orders = self.store.list_for_user(100)

        self.assertEqual(len(orders), 1)
        order = orders[0]
        self.assertEqual(order["status"], "awaiting_payment")
        self.assertEqual(order["amount_irr"], 1_438_000)
        self.assertEqual(self.canboso.balance_calls, 0)
        self.assertEqual(
            self.store.purchase_request(order["order_id"]),
            {"product_id": "prod-1", "quantity": 1},
        )
        self.assertEqual(self.message.sent[-1][1].inline_keyboard[0][0].text, "💳 پرداخت کارت‌به‌کارت")

        await _process_verified_order(self.update, self.context, order)

        self.assertEqual(self.store.get_by_id(order["order_id"])["status"], "sandbox_paid")
        self.assertEqual(self.canboso.purchase_calls, [])

    async def test_checkout_stops_before_payment_when_supplier_wallet_is_insufficient(self):
        self.settings = Settings(**{**self.settings.__dict__, "payexa_mode": "live"})
        self.context.application.bot_data["settings"] = self.settings
        self.canboso.balance_value = Decimal("0")

        await _begin_checkout(self.update, self.context, "prod-1")

        self.assertEqual(self.store.list_for_user(100), [])
        self.assertNotIn("draft", self.context.user_data)
        self.assertIn("امکان ثبت سفارش", self.message.sent[-1][0])
        self.assertNotIn("موجودی دلاری", self.message.sent[-1][0])

    async def test_live_verified_payment_fulfills_once_and_encrypts_delivery(self):
        self.settings = Settings(**{**self.settings.__dict__, "payexa_mode": "live"})
        self.context.application.bot_data["settings"] = self.settings
        order_id = "HCM-LIVE12345678901234567890"
        self.store.create_order(
            {
                "order_id": order_id,
                "user_id": 100,
                "chat_id": 100,
                "product_id": "prod-1",
                "product_name": "Test account",
                "product_type": "account",
                "quantity": 1,
                "amount_irr": 1_438_000,
                "supplier_price_usd": "1.25",
            },
            {"product_id": "prod-1", "quantity": 1},
        )
        self.store.attach_payment(order_id, f"pay-{order_id}")
        order = self.store.get_by_id(order_id)

        await _process_verified_order(self.update, self.context, order)
        await _process_verified_order(self.update, self.context, order)

        saved = self.store.get_by_id(order_id)
        self.assertEqual(saved["status"], "fulfilled")
        self.assertEqual(self.canboso.purchase_calls, [(order_id, {"product_id": "prod-1", "quantity": 1})])
        self.assertEqual(self.store.delivery(order_id, 100)["accounts"][0]["password"], "secret")
        self.assertIsNone(self.store.delivery(order_id, 999))

    async def test_supplier_rejection_marks_order_for_manual_refund_and_alerts_admin(self):
        self.settings = Settings(**{**self.settings.__dict__, "payexa_mode": "live"})
        self.context.application.bot_data["settings"] = self.settings
        self.canboso.purchase_result = {
            "order": {"orderCode": "SUP-FAILED", "status": "failed"},
        }
        order_id = "HCM-FAILED1234567890123456789"
        self.store.create_order(
            {
                "order_id": order_id,
                "user_id": 100,
                "chat_id": 100,
                "product_id": "prod-1",
                "product_name": "Test account",
                "product_type": "account",
                "quantity": 1,
                "amount_irr": 1_438_000,
                "supplier_price_usd": "1.25",
            },
            {"product_id": "prod-1", "quantity": 1},
        )
        self.store.attach_payment(order_id, f"pay-{order_id}")

        await _process_verified_order(self.update, self.context, self.store.get_by_id(order_id))

        saved = self.store.get_by_id(order_id)
        self.assertEqual(saved["status"], "refund_required")
        self.assertEqual(saved["error_code"], "SUPPLIER_ORDER_FAILED")
        self.assertEqual(self.admin_messages[0][0], 123)
        self.assertIn(order_id, self.admin_messages[0][1])

    async def test_mismatched_payment_amount_does_not_create_supplier_purchase(self):
        order_id = "HCM-MISMATCH1234567890123456"
        self.store.create_order(
            {
                "order_id": order_id,
                "user_id": 100,
                "chat_id": 100,
                "product_id": "prod-1",
                "product_name": "Test account",
                "product_type": "account",
                "quantity": 1,
                "amount_irr": 1_438_000,
                "supplier_price_usd": "1.25",
            },
            {"product_id": "prod-1", "quantity": 1},
        )
        self.store.attach_payment(order_id, f"pay-{order_id}")
        self.payexa.amount = 10

        await _process_verified_order(self.update, self.context, self.store.get_by_id(order_id))

        self.assertEqual(self.store.get_by_id(order_id)["status"], "awaiting_payment")
        self.assertEqual(self.canboso.purchase_calls, [])

    async def test_fractional_payment_amount_is_not_truncated_into_a_match(self):
        order_id = "HCM-FRACTION123456789012345678"
        self.store.create_order(
            {
                "order_id": order_id,
                "user_id": 100,
                "chat_id": 100,
                "product_id": "prod-1",
                "product_name": "Test account",
                "product_type": "account",
                "quantity": 1,
                "amount_irr": 1_438_000,
                "supplier_price_usd": "1.25",
            },
            {"product_id": "prod-1", "quantity": 1},
        )
        self.store.attach_payment(order_id, f"pay-{order_id}")
        self.payexa.amount = 1_438_000.9

        await _process_verified_order(self.update, self.context, self.store.get_by_id(order_id))

        self.assertEqual(self.store.get_by_id(order_id)["status"], "awaiting_payment")
        self.assertEqual(self.canboso.purchase_calls, [])

    async def test_business_slot_email_month_selection_creates_api_contract_payload(self):
        product = {
            **PRODUCT,
            "productId": "slot_chatgpt_business",
            "name": "ChatGPT Business Slot",
            "productType": "slot",
            "purchaseRequirements": {
                "customerEmail": True,
                "slotMonths": True,
                "allowedMonths": [1, 3, 6, 12],
            },
        }
        self.canboso = FakeCanboso(product=product)
        self.context.application.bot_data["canboso"] = self.canboso

        await _begin_checkout(self.update, self.context, "slot_chatgpt_business")
        self.assertEqual(self.context.user_data["draft"]["expected"], "email")
        prompt_markup = self.message.sent[-1][1]
        self.assertIsNotNone(prompt_markup)
        self.assertEqual(prompt_markup.inline_keyboard[-1][0].callback_data, "cancel")
        self.assertEqual(prompt_markup.inline_keyboard[-1][0].style, "danger")

        self.message.text = "buyer@example.com"
        await _collect_requirement(self.update, self.context)
        month_markup = self.message.sent[-1][1]
        self.assertEqual(
            [button.text for row in month_markup.inline_keyboard[:-1] for button in row],
            ["1 ماه", "3 ماه", "6 ماه", "12 ماه"],
        )
        self.assertEqual(month_markup.inline_keyboard[-1][0].callback_data, "cancel")

        self.query.data = "month:3"
        await callback(self.update, self.context)

        order = self.store.list_for_user(100)[0]
        self.assertEqual(
            self.store.purchase_request(order["order_id"]),
            {
                "product_id": "slot_chatgpt_business",
                "quantity": 1,
                "customer_email": "buyer@example.com",
                "slot_months": 3,
            },
        )

    async def test_old_waiting_order_uses_exact_supplier_lookup_when_recent_history_omits_it(self):
        order_id = "HCM-OLD1234567890123456789012"
        self.store.create_order(
            {
                "order_id": order_id,
                "user_id": 100,
                "chat_id": 100,
                "product_id": "upgrade-1",
                "product_name": "Account upgrade",
                "product_type": "upgrade_account",
                "quantity": 1,
                "amount_irr": 100_000,
                "supplier_price_usd": "1",
            },
            {"product_id": "upgrade-1", "customer_accounts": ["target-account"]},
        )
        self.store.attach_payment(order_id, "pay-old")
        self.store.mark_paid(order_id)
        self.store.begin_supplier_purchase(order_id)
        self.store.save_purchase_result(order_id, "waiting_supplier", "SUP-OLD", "paid", None)
        self.canboso.order_history["SUP-OLD"] = [
            {"orderCode": "SUP-OLD", "status": "completed"},
        ]
        self.update.callback_query = None

        await _show_orders(self.update, self.context)

        self.assertEqual(self.store.get_by_id(order_id)["status"], "fulfilled")
        self.assertIn("SUP-OLD", self.canboso.order_queries)

    async def test_card_checkout_adds_random_offset_and_shows_card_details(self):
        self.settings = Settings(
            **{
                **self.settings.__dict__,
                "payment_method": "card",
                "card_number": "6037991812345678",
                "card_holder_name": "رضا رضایی",
            }
        )
        self.context.application.bot_data["settings"] = self.settings

        await _begin_checkout(self.update, self.context, "prod-1")
        # Base quote for 1.25 USD at 1,000,000 IRR with 1500 bps (15%) is 1,438,000 IRR (143,800 Tomans)
        base_irr = _quote_irr(Decimal("1.25"), self.settings)

        orders = self.store.list_for_user(100)
        self.assertEqual(len(orders), 1)
        created_order = orders[0]
        self.assertEqual(created_order["status"], "awaiting_payment")
        self.assertIsNone(created_order["payment_id"])

        # Offset between 110 and 990 Tomans (1,100 to 9,900 IRR)
        self.assertGreater(created_order["amount_irr"], base_irr)
        self.assertLessEqual(created_order["amount_irr"], base_irr + 9900)

        # Message sent to user contains card number, holder name, base rounded price, Rial amount, and copy buttons
        last_message = self.message.sent[-1]
        text, markup = last_message[0], last_message[1]
        self.assertIn("6037991812345678", text)
        self.assertIn("رضا رضایی", text)
        self.assertIn("قیمت گرد شده محصول", text)
        self.assertIn(str(created_order["amount_irr"]), text)

        # First row has copy buttons for card and Rial amount
        self.assertEqual(markup.inline_keyboard[0][0].copy_text.text, "6037991812345678")
        self.assertEqual(markup.inline_keyboard[0][1].copy_text.text, str(created_order["amount_irr"]))

        # Second row has cancel order button
        self.assertEqual(markup.inline_keyboard[1][0].callback_data, f"cancel_order:{created_order['order_id']}")

        # Clicking pay_info callback re-displays the card payment screen with copy buttons
        async def async_answer(*a, **k):
            pass
        pay_info_query = SimpleNamespace(
            message=self.message,
            data=f"pay_info:{created_order['order_id']}",
            answer=async_answer,
            edit_message_text=self.message.edit_message_text,
        )
        pay_info_update = SimpleNamespace(
            effective_user=self.user,
            effective_chat=self.chat,
            effective_message=self.message,
            callback_query=pay_info_query,
        )
        await callback(pay_info_update, self.context)
        last_edit = self.message.sent[-1]
        self.assertIn("6037991812345678", last_edit[0])
        self.assertEqual(last_edit[1].inline_keyboard[0][0].copy_text.text, "6037991812345678")
        self.assertEqual(last_edit[1].inline_keyboard[0][1].copy_text.text, str(created_order["amount_irr"]))

    async def test_receipt_submission_and_admin_approval_fulfills_order(self):
        self.settings = Settings(
            **{
                **self.settings.__dict__,
                "payexa_mode": "live",
                "payment_method": "card",
                "card_number": "6037991812345678",
                "card_holder_name": "رضا رضایی",
            }
        )
        self.context.application.bot_data["settings"] = self.settings
        order_id = "HCM-CARDTEST1234567890123456"
        self.store.create_order(
            {
                "order_id": order_id,
                "user_id": 100,
                "chat_id": 100,
                "product_id": "prod-1",
                "product_name": "Test account",
                "product_type": "account",
                "quantity": 1,
                "amount_irr": 1_438_420,
                "supplier_price_usd": "1.25",
            },
            {"product_id": "prod-1", "quantity": 1},
        )

        # Customer sends photo receipt
        photo_item = SimpleNamespace(file_id="photo-file-id-123")
        receipt_message = FakeMessage(photo=[photo_item])
        receipt_update = SimpleNamespace(
            effective_user=self.user,
            effective_chat=self.chat,
            effective_message=receipt_message,
        )
        await receipt_handler(receipt_update, self.context)

        # Order status should now be payment_review
        saved = self.store.get_by_id(order_id)
        self.assertEqual(saved["status"], "payment_review")

        # Admin received the photo with approve/reject buttons
        self.assertEqual(len(self.admin_photos), 1)
        admin_chat_id, photo_id, caption, markup = self.admin_photos[0]
        self.assertEqual(admin_chat_id, 123)
        self.assertEqual(photo_id, "photo-file-id-123")
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, f"admin_approve:{order_id}")
        self.assertEqual(markup.inline_keyboard[0][1].callback_data, f"admin_reject:{order_id}")

        # Customer received confirmation message
        self.assertIn("رسید در صف بررسی", receipt_message.sent[-1][0])

        # Admin clicks approve
        admin_user = SimpleNamespace(id=123, username="admin_user")
        async def admin_answer(text=None, show_alert=False):
            pass
        async def edit_caption(text, reply_markup=None):
            self.message.sent.append((text, reply_markup))
        async def edit_text(text, reply_markup=None):
            self.message.sent.append((text, reply_markup))

        admin_query = SimpleNamespace(
            message=self.message,
            data=f"admin_approve:{order_id}",
            answer=admin_answer,
            edit_message_caption=edit_caption,
            edit_message_text=edit_text,
        )
        admin_update = SimpleNamespace(
            effective_user=admin_user,
            effective_chat=SimpleNamespace(id=123, type=ChatType.PRIVATE),
            effective_message=self.message,
            callback_query=admin_query,
        )
        await callback(admin_update, self.context)

        # Order must now be fulfilled with credentials delivered
        final_order = self.store.get_by_id(order_id)
        self.assertEqual(final_order["status"], "fulfilled")
        self.assertEqual(self.canboso.purchase_calls, [(order_id, {"product_id": "prod-1", "quantity": 1})])
        self.assertEqual(self.store.delivery(order_id, 100)["accounts"][0]["password"], "secret")

    async def test_receipt_submission_and_admin_rejection_marks_failed(self):
        self.settings = Settings(
            **{
                **self.settings.__dict__,
                "payment_method": "card",
                "card_number": "6037991812345678",
                "card_holder_name": "رضا رضایی",
            }
        )
        self.context.application.bot_data["settings"] = self.settings
        order_id = "HCM-REJECT1234567890123456"
        self.store.create_order(
            {
                "order_id": order_id,
                "user_id": 100,
                "chat_id": 100,
                "product_id": "prod-1",
                "product_name": "Test account",
                "product_type": "account",
                "quantity": 1,
                "amount_irr": 1_438_420,
                "supplier_price_usd": "1.25",
            },
            {"product_id": "prod-1", "quantity": 1},
        )
        self.store.update_status(order_id, "payment_review")

        admin_user = SimpleNamespace(id=123, username="admin_user")
        async def admin_reject_answer(text=None, show_alert=False):
            pass
        async def edit_caption(text, reply_markup=None):
            self.message.sent.append((text, reply_markup))
        async def edit_text(text, reply_markup=None):
            self.message.sent.append((text, reply_markup))

        admin_query = SimpleNamespace(
            message=self.message,
            data=f"admin_reject:{order_id}",
            answer=admin_reject_answer,
            edit_message_caption=edit_caption,
            edit_message_text=edit_text,
        )
        admin_update = SimpleNamespace(
            effective_user=admin_user,
            effective_chat=SimpleNamespace(id=123, type=ChatType.PRIVATE),
            effective_message=self.message,
            callback_query=admin_query,
        )
        await callback(admin_update, self.context)

        # Order status must be payment_failed with error_code ADMIN_REJECTED
        final_order = self.store.get_by_id(order_id)
        self.assertEqual(final_order["status"], "payment_failed")
        self.assertEqual(final_order["error_code"], "ADMIN_REJECTED")
        self.assertEqual(len(self.canboso.purchase_calls), 0)

        # Customer received notification
        self.assertTrue(any("مورد تأیید قرار نگرفت" in text for chat_id, text, _ in self.admin_messages if chat_id == 100))

    async def test_non_admin_cannot_approve_receipt(self):
        order_id = "HCM-NONADMIN12345678901234"
        self.store.create_order(
            {
                "order_id": order_id,
                "user_id": 100,
                "chat_id": 100,
                "product_id": "prod-1",
                "product_name": "Test account",
                "product_type": "account",
                "quantity": 1,
                "amount_irr": 1_438_420,
                "supplier_price_usd": "1.25",
            },
            {"product_id": "prod-1", "quantity": 1},
        )
        self.store.update_status(order_id, "payment_review")

        # Hacker/non-admin user (id: 999) tries to click admin_approve
        non_admin_user = SimpleNamespace(id=999, username="hacker")
        answers = []
        async def non_admin_answer(text=None, show_alert=False):
            answers.append((text, show_alert))

        non_admin_query = SimpleNamespace(
            message=self.message,
            data=f"admin_approve:{order_id}",
            answer=non_admin_answer,
        )
        update = SimpleNamespace(
            effective_user=non_admin_user,
            effective_chat=SimpleNamespace(id=999, type=ChatType.PRIVATE),
            effective_message=self.message,
            callback_query=non_admin_query,
        )
        await callback(update, self.context)

        # Order must remain payment_review, Canboso purchase NOT called
        self.assertEqual(self.store.get_by_id(order_id)["status"], "payment_review")
        self.assertEqual(len(self.canboso.purchase_calls), 0)
        self.assertTrue(any("شما دسترسی مدیریت ندارید" in (item[0] or "") for item in answers))

    async def test_user_can_cancel_unpaid_order(self):
        order_id = "HCM-CANCEL123456789012345"
        self.store.create_order(
            {
                "order_id": order_id,
                "user_id": 100,
                "chat_id": 100,
                "product_id": "prod-1",
                "product_name": "Test account",
                "product_type": "account",
                "quantity": 1,
                "amount_irr": 1_438_420,
                "supplier_price_usd": "1.25",
            },
            {"product_id": "prod-1", "quantity": 1},
        )
        async def cancel_answer(text=None, show_alert=False):
            pass
        async def cancel_edit_text(text, reply_markup=None, *args, **kwargs):
            self.message.sent.append((text, reply_markup))

        cancel_query = SimpleNamespace(
            message=self.message,
            data=f"cancel_order:{order_id}",
            answer=cancel_answer,
            edit_message_text=cancel_edit_text,
        )
        update = SimpleNamespace(
            effective_user=self.user,
            effective_chat=self.chat,
            effective_message=self.message,
            callback_query=cancel_query,
        )
        await callback(update, self.context)

        saved = self.store.get_by_id(order_id)
        self.assertEqual(saved["status"], "payment_failed")
        self.assertEqual(saved["error_code"], "USER_CANCELLED")


if __name__ == "__main__":
    unittest.main()
