from __future__ import annotations

import logging
import re
import secrets
import uuid
from decimal import Decimal, ROUND_CEILING
from typing import Any

from telegram import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .canboso import CanbosoClient, CanbosoError
from .config import Settings
from .payexa import PayexaClient, PayexaError
from .storage import OrderStore


logger = logging.getLogger("hamechi_market")
PAGE_SIZE = 6
SUPPORT_URL = "https://t.me/EZPY9798"
TYPE_LABELS = {"account": "اکانت", "slot": "اشتراک گروهی", "upgrade_account": "ارتقای اکانت"}
BRAND_LABELS = {
    "chatgpt": "ChatGPT",
    "elevenlabs": "ElevenLabs",
    "capcut": "CapCut",
    "microsoft_office_365": "Microsoft 365",
    "google_one": "Gemini",
    "youtube": "YouTube",
    "antigravity": "Veo / Antigravity",
    "apigpt": "Codex Astra API",
    "higgfield": "Higgsfield",
    "muse_ai": "Muse AI",
    "hma_vpn": "HMA VPN",
    "express": "Express VPN",
    "proton": "Proton VPN",
    "nordvpn": "NordVPN",
    "surfshark": "Surfshark",
    "icloud": "iCloud",
    "linkedin": "LinkedIn",
    "gpm_login": "GPM Login",
    "tele": "ابزارهای تلگرام",
    "decor_discord": "Discord Decor",
}
STATUS_LABELS = {
    "awaiting_payment": "در انتظار پرداخت",
    "paid": "پرداخت تأیید شد؛ در حال ثبت سفارش",
    "submitting": "در حال ثبت سفارش",
    "fulfilled": "تحویل شده",
    "waiting_supplier": "در انتظار تکمیل توسط تأمین‌کننده",
    "refund_required": "نیازمند پیگیری و بازپرداخت",
    "payment_review": "در انتظار بررسی رسید توسط مدیر",
    "payment_failed": "پرداخت ناموفق یا لغو شد",
    "payment_expired": "مهلت پرداخت تمام شد",
    "sandbox_paid": "پرداخت آزمایشی تأیید شد؛ سفارش واقعی ثبت نشد",
}
SUPPLIER_WAITING = {"paid", "pending", "processing"}
SUPPLIER_FAILED = {"failed", "failure", "cancelled", "canceled", "rejected", "refunded", "error"}


def _is_private(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.type == ChatType.PRIVATE)


def _available(product: dict[str, Any]) -> int:
    stock = product.get("availability")
    if not isinstance(stock, dict):
        return 0
    try:
        return max(0, int(stock.get("available") or 0))
    except (TypeError, ValueError):
        return 0


def _brand_category(product: dict[str, Any]) -> str:
    value = str(product.get("emoji") or "").strip().casefold()
    return value if value != "none" and re.fullmatch(r"[a-z0-9_-]{1,32}", value) else "other"


def _brand_label(category: str) -> str:
    if category == "other":
        return "سایر محصولات"
    if category in BRAND_LABELS:
        return BRAND_LABELS[category]
    return " ".join(
        word.upper() if word in {"ai", "api", "vpn", "id", "ios", "us", "gpm"} else word.title()
        for word in category.split("_")
    )


def _search_text(value: Any) -> str:
    return str(value or "").casefold().replace("ي", "ی").replace("ك", "ک")


def _usd_price(product: dict[str, Any]) -> Decimal | None:
    price = product.get("price")
    if not isinstance(price, dict) or str(price.get("currency", "")).upper() != "USD":
        return None
    try:
        amount = Decimal(str(price.get("amount")))
    except Exception:
        return None
    return amount if amount.is_finite() and amount > 0 else None


def _quote_irr(price_usd: Decimal, settings: Settings) -> int | None:
    if settings.usd_to_irr_rate is None or settings.seller_margin_bps is None:
        return None
    total = price_usd * settings.usd_to_irr_rate * Decimal(10_000 + settings.seller_margin_bps) / Decimal(10_000)
    return int((total / Decimal(1_000)).to_integral_value(rounding=ROUND_CEILING) * 1_000)


def _toman(amount_irr: int) -> str:
    amount_toman = amount_irr // 10
    return f"{amount_toman:,} تومان"


def _product_sale_price(product: dict[str, Any], settings: Settings) -> str:
    price = _usd_price(product)
    amount = _quote_irr(price, settings) if price is not None else None
    return _toman(amount) if amount is not None else "قیمت در حال تنظیم"


def _supplier_outcome(order: dict[str, Any]) -> tuple[str, str | None]:
    status = str(order.get("status") or "").casefold()
    fulfillment = str(order.get("fulfillmentStatus") or "").casefold()
    if status in SUPPLIER_FAILED or fulfillment in SUPPLIER_FAILED:
        return "refund_required", "SUPPLIER_ORDER_FAILED"
    if fulfillment == "waiting_seller" or (status in SUPPLIER_WAITING and fulfillment not in {"completed", "fulfilled"}):
        return "waiting_supplier", None
    if status == "completed" or fulfillment in {"completed", "fulfilled", "autocompleted"}:
        return "fulfilled", None
    return "refund_required", "UNKNOWN_SUPPLIER_STATUS"


async def _catalog(context: ContextTypes.DEFAULT_TYPE, force: bool = False) -> list[dict[str, Any]]:
    now = context.application.bot_data.get("catalog_loaded_at", 0.0)
    if not force and context.application.bot_data.get("catalog") is not None:
        import time

        if time.monotonic() - now < 60:
            return context.application.bot_data["catalog"]
    products = await context.application.bot_data["canboso"].products()
    context.application.bot_data["catalog"] = products
    import time

    context.application.bot_data["catalog_loaded_at"] = time.monotonic()
    return products


def _main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🛍️ دیدن دسته‌بندی‌ها", callback_data="catalog", style="primary")],
            [InlineKeyboardButton("📦 سفارش‌های من", callback_data="orders")],
            [InlineKeyboardButton("💬 پشتیبانی", url=SUPPORT_URL)],
        ]
    )


def _home_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🛍️ محصولات"), KeyboardButton("📦 سفارش‌ها")],
            [KeyboardButton("🔎 جست‌وجو"), KeyboardButton("❓ راهنما")],
            [KeyboardButton("💬 پشتیبانی")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="یک گزینه را انتخاب کن",
    )


def _cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("لغو خرید", callback_data="cancel", style="danger")]]
    )


def _search_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("لغو جست‌وجو", callback_data="search:cancel", style="danger")]]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update) or not update.effective_message:
        return
    context.user_data.pop("draft", None)
    if context.args:
        payload = context.args[0]
        await _verify_payment(update, context, payload.removeprefix("pay_"))
        return
    await update.effective_message.reply_text(
        "سلام! به HameChiMarket خوش اومدی 👋\n"
        "محصولت رو انتخاب کن، قیمت نهایی رو به تومان ببین و سفارشت رو همین‌جا پیگیری کن.",
        reply_markup=_home_keyboard(),
    )


async def products_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if _is_private(update):
        await _show_catalog_entry(update, context)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update) or not update.effective_message:
        return
    await update.effective_message.reply_text(
        "راهنمای خرید\n"
        "۱) محصولات را باز کن و دسته یا محصول دلخواهت را بزن.\n"
        "۲) جزئیات و قیمت نهایی را ببین و ادامهٔ خرید را انتخاب کن.\n"
        "۳) اطلاعات خواسته‌شده را وارد کن و پرداخت را در درگاه انجام بده.\n"
        "وضعیت سفارش از بخش سفارش‌های من قابل پیگیری است. برای لغو پیش از ساخت سفارش، /cancel را بفرست.",
        reply_markup=_home_keyboard(),
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update) or not update.effective_message:
        return
    draft = context.user_data.pop("draft", None)
    searching = context.user_data.pop("awaiting_product_search", False)
    context.user_data.pop("product_search_query", None)
    text = (
        "مرحلهٔ خرید لغو شد؛ هنوز سفارشی ساخته نشده است."
        if draft is not None
        else "جست‌وجو لغو شد."
        if searching
        else "خریدی در حال تکمیل نیست. سفارش‌های قبلی از بخش سفارش‌های من قابل پیگیری‌اند."
    )
    await update.effective_message.reply_text(text, reply_markup=_home_keyboard())


async def _show_catalog_entry(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    edit: bool = False,
) -> None:
    try:
        products = await _catalog(context)
    except CanbosoError as exc:
        logger.warning("Canboso catalog failed: %s", exc.code)
        text = "فعلاً دریافت فهرست محصولات ممکن نیست. کمی بعد دوباره تلاش کن."
        if edit and update.callback_query:
            await _safe_edit(update.callback_query, text, _main_keyboard())
        elif update.effective_message:
            await update.effective_message.reply_text(text, reply_markup=_home_keyboard())
        return

    context.user_data.pop("product_search_query", None)
    context.user_data.pop("awaiting_product_search", None)
    available = [product for product in products if _available(product) > 0]
    counts: dict[str, int] = {}
    for product in available:
        category = _brand_category(product)
        counts[category] = counts.get(category, 0) + 1
    categories = sorted(
        counts,
        key=lambda category: (category == "other", -counts[category], _brand_label(category).casefold()),
    )
    context.user_data["catalog_location"] = ("categories", 0)
    category_buttons = [
        InlineKeyboardButton(
            f"{_brand_label(category)} · {counts[category]}",
            callback_data=f"list:brand-{category}:0",
        )
        for category in categories
    ]
    rows = [category_buttons[index : index + 2] for index in range(0, len(category_buttons), 2)]
    rows.append(
        [
            InlineKeyboardButton(f"همهٔ محصولات · {len(available)}", callback_data="list:all:0"),
            InlineKeyboardButton("🔎 جست‌وجو", callback_data="search:start"),
        ]
    )
    markup = InlineKeyboardMarkup(rows)
    text = (
        "🛍️ محصولات بر اساس سرویس\n"
        f"{len(available)} محصول موجود است. یک سرویس را انتخاب کن یا محصول را جست‌وجو کن."
    )
    if edit and update.callback_query:
        await _safe_edit(update.callback_query, text, markup)
    elif update.effective_message:
        await update.effective_message.reply_text(text, reply_markup=markup)


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update) or not update.effective_message or not update.effective_message.text:
        return
    message = update.effective_message
    value = message.text.strip()
    menu_actions = {
        "🛍️ محصولات": "products",
        "📦 سفارش‌ها": "orders",
        "🔎 جست‌وجو": "search",
        "❓ راهنما": "help",
        "💬 پشتیبانی": "support",
    }
    if context.user_data.get("awaiting_product_search"):
        if value not in menu_actions:
            if len(value) < 2:
                await message.reply_text("برای جست‌وجو دست‌کم دو حرف بفرست.")
                return
            context.user_data.pop("awaiting_product_search", None)
            context.user_data["product_search_query"] = value
            await _show_catalog(update, context, "search", 0)
            return
        context.user_data.pop("awaiting_product_search", None)

    action = menu_actions.get(value)
    if action == "products":
        if context.user_data.get("draft"):
            await message.reply_text("برای ادامهٔ خرید اطلاعات خواسته‌شده را بفرست یا /cancel را بزن.")
            return
        await _show_catalog_entry(update, context)
    elif action == "orders":
        await _show_orders(update, context)
    elif action == "search":
        if context.user_data.get("draft"):
            await message.reply_text("برای ادامهٔ خرید اطلاعات خواسته‌شده را بفرست یا /cancel را بزن.")
            return
        context.user_data["awaiting_product_search"] = True
        await message.reply_text(
            "نام محصول یا بخشی از توضیحاتش را بفرست تا در محصولات موجود بگردم.",
            reply_markup=_search_cancel_keyboard(),
        )
    elif action == "help":
        await help_command(update, context)
    elif action == "support":
        await message.reply_text(
            "برای گفت‌وگو با پشتیبانی روی دکمهٔ زیر بزن.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("💬 ارتباط با پشتیبانی", url=SUPPORT_URL)]]
            ),
        )
    else:
        await _collect_requirement(update, context)


async def _show_catalog(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    product_type: str,
    page: int,
    edit: bool = False,
) -> None:
    try:
        products = await _catalog(context)
    except CanbosoError as exc:
        logger.warning("Canboso catalog failed: %s", exc.code)
        text = "فعلاً دریافت فهرست محصولات ممکن نیست. کمی بعد دوباره تلاش کن."
        if edit and update.callback_query and update.callback_query.message:
            await _safe_edit(update.callback_query, text, _main_keyboard())
        elif update.effective_message:
            await update.effective_message.reply_text(text, reply_markup=_main_keyboard())
        return

    search_query = str(context.user_data.get("product_search_query") or "").strip()
    if product_type == "search" and not search_query:
        await _show_catalog_entry(update, context, edit=edit)
        return
    if product_type == "search":
        needle = _search_text(search_query)
        shown = [
            product
            for product in products
            if _available(product) > 0
            and any(needle in _search_text(product.get(field)) for field in ("name", "description"))
        ]
    elif product_type.startswith("brand-"):
        brand = product_type.removeprefix("brand-")
        shown = [
            product
            for product in products
            if _available(product) > 0 and _brand_category(product) == brand
        ]
    else:
        context.user_data.pop("product_search_query", None)
        shown = [
            product
            for product in products
            if _available(product) > 0
            and (product_type == "all" or product.get("productType") == product_type)
        ]
    settings: Settings = context.application.bot_data["settings"]
    page_count = max(1, (len(shown) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, page_count - 1))
    context.user_data["catalog_location"] = (product_type, page)
    page_items = shown[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    type_title = (
        f"نتایج «{search_query[:35]}»"
        if product_type == "search"
        else _brand_label(product_type.removeprefix("brand-"))
        if product_type.startswith("brand-")
        else {"all": "همهٔ محصولات", **TYPE_LABELS}.get(product_type, "محصولات")
    )
    lines = [f"{type_title} · صفحهٔ {page + 1} از {page_count}", "قیمت نهایی به تومان · برای جزئیات، محصول را انتخاب کن:"]
    rows: list[list[InlineKeyboardButton]] = []
    product_buttons = []
    for index, product in enumerate(page_items, page * PAGE_SIZE + 1):
        name = str(product.get("name") or "محصول")
        lines.append(f"{index}. {name[:120]} — {_product_sale_price(product, settings)}")
        product_buttons.append(
            InlineKeyboardButton(f"{index}. {name[:22]}", callback_data=f"prod:{product.get('productId', '')}")
        )
    rows.extend(
        [product_buttons[index : index + 2] for index in range(0, len(product_buttons), 2)]
    )
    rows.append(
        [
            InlineKeyboardButton("همهٔ محصولات", callback_data="list:all:0"),
            InlineKeyboardButton("🔎 جست‌وجو", callback_data="search:start"),
        ]
    )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("قبلی", callback_data=f"list:{product_type}:{page - 1}"))
    if page + 1 < page_count:
        nav.append(InlineKeyboardButton("بعدی", callback_data=f"list:{product_type}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("بازگشت به دسته‌بندی‌ها", callback_data="catalog")])
    markup = InlineKeyboardMarkup(rows)
    text = "\n".join(lines) if page_items else f"{type_title} فعلاً موجود نیست. نام دیگری را جست‌وجو کن."
    if edit and update.callback_query:
        await _safe_edit(update.callback_query, text, markup)
    elif update.effective_message:
        await update.effective_message.reply_text(text, reply_markup=markup)


async def _show_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: str) -> None:
    query = update.callback_query
    if not query:
        return
    try:
        products = await _catalog(context)
    except CanbosoError as exc:
        logger.warning("Canboso catalog failed: %s", exc.code)
        await _safe_edit(query, "جزئیات محصول فعلاً در دسترس نیست.", _main_keyboard())
        return
    product = next((p for p in products if str(p.get("productId")) == product_id), None)
    if not product:
        await _safe_edit(query, "این محصول دیگر در فهرست نیست.", _main_keyboard())
        return
    description = str(product.get("description") or "توضیحی ثبت نشده است.")
    if len(description) > 2_800:
        description = description[:2_800].rstrip() + "…"
    product_type = TYPE_LABELS.get(str(product.get("productType")), str(product.get("productType") or "نامشخص"))
    settings: Settings = context.application.bot_data["settings"]
    text = (
        f"{product.get('name', 'محصول')}\n"
        f"نوع: {product_type}\n"
        f"قیمت فروش: {_product_sale_price(product, settings)}\n"
        f"موجودی فعلی: {_available(product)}\n\n"
        f"توضیحات تأمین‌کننده:\n{description}"
    )
    rows = []
    if _available(product) > 0:
        rows.append([InlineKeyboardButton("🛒 ادامهٔ خرید", callback_data=f"buy:{product_id}", style="success")])
    product_type_filter, page = context.user_data.get("catalog_location", ("all", 0))
    rows.append(
        [InlineKeyboardButton("بازگشت به فهرست", callback_data=f"list:{product_type_filter}:{page}")]
    )
    await _safe_edit(query, text, InlineKeyboardMarkup(rows))


async def _begin_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: str) -> None:
    query = update.callback_query
    settings: Settings = context.application.bot_data["settings"]
    if not query or not update.effective_user:
        return
    if not settings.checkout_ready:
        await _safe_edit(
            query,
            "خرید آنلاین هنوز راه‌اندازی نشده است. فعلاً می‌توانی محصولات را مرور کنی.",
            _main_keyboard(),
        )
        return
    try:
        products = await _catalog(context, force=True)
        if settings.payexa_mode == "live":
            currency, balance = await context.application.bot_data["canboso"].balance()
    except CanbosoError as exc:
        logger.warning("Canboso checkout preflight failed: %s", exc.code)
        await _safe_edit(query, "بررسی موجودی تأمین‌کننده ناموفق بود. بعداً دوباره تلاش کن.", _main_keyboard())
        return
    product = next((p for p in products if str(p.get("productId")) == product_id), None)
    if not product or _available(product) < 1:
        await _safe_edit(query, "این محصول فعلاً موجود نیست.", _main_keyboard())
        return
    price = _usd_price(product)
    if price is None or (settings.payexa_mode == "live" and currency != "USD"):
        await _safe_edit(query, "واحد قیمت محصول یا کیف پول پشتیبانی نمی‌شود؛ سفارش متوقف شد.", _main_keyboard())
        return
    if settings.payexa_mode == "live" and balance < price:
        await _safe_edit(query, "امکان ثبت سفارش این محصول فعلاً فراهم نیست؛ کمی بعد دوباره تلاش کن.", _main_keyboard())
        return
    amount_irr = _quote_irr(price, settings)
    if amount_irr is None or amount_irr < 10_000:
        await _safe_edit(query, "قیمت‌گذاری این محصول هنوز آماده نیست.", _main_keyboard())
        return

    raw_requirements = product.get("purchaseRequirements")
    requirements = raw_requirements if isinstance(raw_requirements, dict) else {}
    if raw_requirements is not None and not isinstance(raw_requirements, dict):
        await _safe_edit(query, "نیازمندی‌های این محصول معتبر نیست؛ خرید متوقف شد.", _main_keyboard())
        return
    if product.get("productType") not in TYPE_LABELS:
        await _safe_edit(query, "نوع این محصول هنوز در خرید پشتیبانی نمی‌شود.", _main_keyboard())
        return
    known_requirements = {"customerEmail", "customerAccounts", "slotMonths", "quantityFixed", "allowedMonths"}
    unsupported = any(name not in known_requirements and value for name, value in requirements.items())
    invalid_flags = any(
        name in requirements and not isinstance(requirements[name], bool)
        for name in ("customerEmail", "customerAccounts", "slotMonths")
    )
    if (
        unsupported
        or invalid_flags
        or (requirements.get("customerAccounts") and product.get("productType") != "upgrade_account")
        or (requirements.get("slotMonths") and product_id != "slot_chatgpt_business")
        or ("allowedMonths" in requirements and product_id != "slot_chatgpt_business")
    ):
        await _safe_edit(query, "یکی از نیازمندی‌های این محصول در خرید پشتیبانی نمی‌شود.", _main_keyboard())
        return
    try:
        fixed_quantity = requirements.get("quantityFixed", 1)
        quantity = int(1 if fixed_quantity is None else fixed_quantity)
    except (TypeError, ValueError):
        quantity = 0
    if quantity != 1:
        await _safe_edit(query, "تعداد ثابت این محصول در نسخهٔ فعلی پشتیبانی نمی‌شود.", _main_keyboard())
        return
    draft = {
        "product_id": product_id,
        "product_name": str(product.get("name") or "محصول"),
        "product_type": str(product.get("productType") or "account"),
        "supplier_price_usd": str(price),
        "amount_irr": amount_irr,
        "requirements": requirements,
        "purchase": {"product_id": product_id, "quantity": quantity},
        "creating_payment": False,
    }
    needs_email = (
        bool(requirements.get("customerEmail"))
        or draft["product_type"] == "slot"
        or product_id == "slot_chatgpt_business"
    )
    needs_account = bool(requirements.get("customerAccounts")) or draft["product_type"] == "upgrade_account"
    needs_months = product_id == "slot_chatgpt_business"
    draft["needs_account"] = needs_account
    draft["needs_months"] = needs_months
    context.user_data["draft"] = draft
    if needs_email:
        draft["expected"] = "email"
        await query.message.reply_text(
            "ایمیلی را که برای دریافت محصول استفاده می‌شود بفرست.", reply_markup=_cancel_keyboard()
        )
    elif needs_account:
        draft["expected"] = "account"
        await query.message.reply_text(
            "ایمیل یا شناسهٔ اکانتی را که باید ارتقا پیدا کند بفرست.", reply_markup=_cancel_keyboard()
        )
    elif needs_months:
        await _ask_months(query.message, draft)
    else:
        await _create_order_and_payment(update, context)


async def _ask_months(message: Any, draft: dict[str, Any]) -> None:
    allowed = draft["requirements"].get("allowedMonths")
    months = [int(m) for m in allowed if str(m).isdigit()] if isinstance(allowed, list) else []
    if not months:
        await message.reply_text("مدت‌های مجاز این محصول از تأمین‌کننده دریافت نشد.", reply_markup=_cancel_keyboard())
        return
    options = [InlineKeyboardButton(f"{month} ماه", callback_data=f"month:{month}") for month in months[:4]]
    rows = [options[index : index + 2] for index in range(0, len(options), 2)]
    rows.append(_cancel_keyboard().inline_keyboard[0])
    await message.reply_text("مدت اشتراک را انتخاب کن:", reply_markup=InlineKeyboardMarkup(rows))


async def _collect_requirement(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    draft = context.user_data.get("draft")
    message = update.effective_message
    if not message or not message.text:
        return
    if not draft:
        await message.reply_text("برای شروع، از منوی پایین استفاده کن یا /help را بفرست.", reply_markup=_home_keyboard())
        return
    value = message.text.strip()
    expected = draft.get("expected")
    if expected == "email":
        if len(value) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            await message.reply_text("ایمیل معتبر نیست؛ دوباره بفرست.")
            return
        draft["purchase"]["customer_email"] = value
    elif expected == "account":
        if not 1 <= len(value) <= 500:
            await message.reply_text("شناسه باید بین ۱ تا ۵۰۰ نویسه باشد؛ دوباره بفرست.")
            return
        draft["purchase"]["customer_accounts"] = [value]
    else:
        return

    draft.pop("expected", None)
    if expected == "email" and draft.get("needs_account"):
        draft["expected"] = "account"
        await message.reply_text("حالا ایمیل یا شناسهٔ اکانت را برای ارتقا بفرست.", reply_markup=_cancel_keyboard())
        return
    if draft.get("needs_months"):
        await _ask_months(message, draft)
        return
    await _create_order_and_payment(update, context)


def _card_payment_text_and_markup(
    order_id: str,
    product_name: str,
    base_amount_irr: int | None,
    amount_irr: int,
    settings: Settings,
) -> tuple[str, InlineKeyboardMarkup]:
    card_raw = settings.card_number
    if len(card_raw) == 16 and card_raw.isdigit():
        card_formatted = f"{card_raw[:4]}-{card_raw[4:8]}-{card_raw[8:12]}-{card_raw[12:]}"
    else:
        card_formatted = card_raw

    base_price_line = f"💵 قیمت گرد شده محصول: {_toman(base_amount_irr)}\n" if base_amount_irr else ""
    text = (
        f"🧾 *سفارش `{order_id}` ثبت شد.*\n\n"
        f"📦 محصول: {product_name}\n"
        f"{base_price_line}"
        f"💰 مبلغ نهایی جهت واریز: *{_toman(amount_irr)}*\n"
        f"🔢 مبلغ دقیق به ریال: `{amount_irr}` ریال\n\n"
        "⚠️ *نکته بسیار مهم:* لطفاً *دقیقاً مبلغ ریالی بالا* را واریز کنید تا شناسه واریز شما تطبیق داده شود.\n\n"
        f"💳 شماره کارت (کلیک برای کپی):\n`{card_raw}`\n"
        f"👤 به نام: *{settings.card_holder_name}*\n\n"
        "📸 پس از واریز، *تصویر فیش یا رسید بانکی* را در همین گفت‌وگو ارسال کنید."
    )
    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("💳 کپی شماره کارت", copy_text=CopyTextButton(text=card_raw)),
                InlineKeyboardButton("🔢 کپی مبلغ به ریال", copy_text=CopyTextButton(text=str(amount_irr))),
            ],
            [
                InlineKeyboardButton("❌ انصراف از خرید", callback_data=f"cancel_order:{order_id}"),
                InlineKeyboardButton("پیگیری سفارش‌ها", callback_data="orders"),
            ],
        ]
    )
    return text, markup


async def _create_order_and_payment(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    draft = context.user_data.get("draft")
    if not draft or draft.get("creating_payment") or not update.effective_user or not update.effective_chat:
        return
    settings: Settings = context.application.bot_data["settings"]
    if not settings.checkout_ready:
        context.user_data.pop("draft", None)
        await update.effective_message.reply_text("خرید آنلاین هنوز راه‌اندازی نشده است.")
        return
    draft["creating_payment"] = True
    order_id: str | None = None
    try:
        products = await _catalog(context, force=True)
        product = next((p for p in products if str(p.get("productId")) == draft["product_id"]), None)
        if not product or _available(product) < 1:
            context.user_data.pop("draft", None)
            await update.effective_message.reply_text("موجودی این محصول تغییر کرده؛ سفارش ساخته نشد.")
            return
        current_price = _usd_price(product)
        if current_price is None or str(current_price) != draft["supplier_price_usd"]:
            context.user_data.pop("draft", None)
            await update.effective_message.reply_text("قیمت محصول تغییر کرده؛ دوباره از فهرست انتخابش کن.")
            return
        if settings.payexa_mode == "live":
            currency, balance = await context.application.bot_data["canboso"].balance()
            if currency != "USD" or balance < current_price:
                context.user_data.pop("draft", None)
                await update.effective_message.reply_text("موجودی دلاری تأمین‌کننده کافی نیست؛ سفارش ساخته نشد.")
                return
        base_amount_irr = _quote_irr(current_price, settings)
        if base_amount_irr is None or base_amount_irr < 10_000:
            context.user_data.pop("draft", None)
            await update.effective_message.reply_text("قیمت‌گذاری سفارش آماده نیست.")
            return

        amount_irr = base_amount_irr
        if settings.payment_method == "card":
            random_offset_toman = (secrets.randbelow(89) + 11) * 10
            amount_irr = base_amount_irr + (random_offset_toman * 10)

        order_id = f"HCM-{uuid.uuid4().hex[:24].upper()}"
        store: OrderStore = context.application.bot_data["store"]
        product_name = str(product.get("name") or draft["product_name"])
        store.create_order(
            {
                "order_id": order_id,
                "user_id": update.effective_user.id,
                "chat_id": update.effective_chat.id,
                "product_id": draft["product_id"],
                "product_name": product_name,
                "product_type": str(product.get("productType") or draft["product_type"]),
                "quantity": 1,
                "amount_irr": amount_irr,
                "supplier_price_usd": str(current_price),
            },
            draft["purchase"],
        )

        if settings.payment_method == "card":
            context.user_data.pop("draft", None)
            text, markup = _card_payment_text_and_markup(
                order_id=order_id,
                product_name=product_name,
                base_amount_irr=base_amount_irr,
                amount_irr=amount_irr,
                settings=settings,
            )
            await update.effective_message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
            return

        username = context.application.bot_data["bot_username"]
        if not username:
            store.update_status(order_id, "payment_failed", "BOT_USERNAME_MISSING")
            context.user_data.pop("draft", None)
            await update.effective_message.reply_text("نام کاربری ربات تنظیم نشده؛ پرداخت ساخته نشد.")
            return
        payexa: PayexaClient | None = context.application.bot_data.get("payexa")
        if payexa is None:
            store.update_status(order_id, "payment_failed", "PAYEXA_NOT_CONFIGURED")
            context.user_data.pop("draft", None)
            await update.effective_message.reply_text("درگاه پرداخت هنوز فعال نشده است.")
            return
        payment = await payexa.create_payment(
            amount_irr,
            order_id,
            f"https://t.me/{username}",
            f"HameChiMarket {order_id}",
        )
        store.attach_payment(order_id, payment["payment_id"])
        context.user_data.pop("draft", None)
        await update.effective_message.reply_text(
            f"سفارش {order_id} آمادهٔ پرداخت است.\nقیمت سفارش: {_toman(amount_irr)}\n\n"
            "مبلغ دقیق کارت‌به‌کارت را در بات پرداخت ببین و دقیقاً همان را واریز کن. "
            "بعد از پرداخت، به این ربات برمی‌گردی و سفارش تأیید می‌شود.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("💳 پرداخت کارت‌به‌کارت", url=payment["payment_url"], style="success")],
                    [InlineKeyboardButton("بررسی پرداخت", callback_data=f"verify:{order_id}", style="primary")],
                ]
            ),
        )
    except CanbosoError as exc:
        logger.warning("Canboso preflight failed: %s", exc.code)
        await update.effective_message.reply_text("بررسی نهایی محصول ناموفق بود؛ فعلاً پرداخت نگیر.")
    except PayexaError as exc:
        logger.warning("Payexa payment creation failed: %s", exc.code)
        if order_id:
            context.application.bot_data["store"].update_status(order_id, "payment_failed", exc.code)
        await update.effective_message.reply_text("ساخت لینک پرداخت ناموفق بود؛ پولی از تو دریافت نشده است.")
    except Exception as exc:
        logger.error("Order setup failed (%s)", type(exc).__name__)
        if order_id:
            saved = context.application.bot_data["store"].get_by_id(order_id)
            if not saved or not saved.get("payment_id"):
                context.application.bot_data["store"].update_status(order_id, "payment_failed", "ORDER_SETUP_FAILED")
        await update.effective_message.reply_text("ساخت سفارش ناموفق بود؛ دوباره تلاش کن.")
    finally:
        if context.user_data.get("draft"):
            context.user_data["draft"]["creating_payment"] = False


async def _verify_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, payment_id: str | None = None) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return
    store: OrderStore = context.application.bot_data["store"]
    if payment_id:
        order = store.get_by_payment(payment_id)
    else:
        order = None
    if not order or order["user_id"] != user.id:
        await message.reply_text("سفارش پرداختی پیدا نشد.", reply_markup=_main_keyboard())
        return
    await _process_verified_order(update, context, order)


async def _process_verified_order(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    order: dict[str, Any],
) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message or order["user_id"] != user.id:
        return
    if order["status"] in {"fulfilled", "waiting_supplier"}:
        await message.reply_text(_order_summary(order), reply_markup=_orders_keyboard(order))
        return
    if order["status"] in {"refund_required", "payment_review", "payment_failed", "payment_expired"}:
        await message.reply_text(_order_summary(order), reply_markup=_main_keyboard())
        return
    settings: Settings = context.application.bot_data["settings"]
    payexa: PayexaClient | None = context.application.bot_data.get("payexa")
    if not payexa or not order.get("payment_id"):
        await message.reply_text("اطلاعات پرداخت سفارش ناقص است؛ با پشتیبانی تماس بگیر.")
        return
    try:
        verification = await payexa.verify(order["payment_id"], int(order["amount_irr"]))
    except PayexaError as exc:
        if exc.code == "PAYMENT_NOT_PAID":
            await message.reply_text("پرداخت هنوز تأیید نشده است. بعد از واریز دوباره بررسی کن.")
            return
        if exc.code == "PAYMENT_EXPIRED":
            context.application.bot_data["store"].update_status(order["order_id"], "payment_expired", exc.code)
            await message.reply_text("مهلت پرداخت تمام شده است. سفارش جدیدی بساز.")
            return
        logger.warning("Payexa verification failed: %s", exc.code)
        await message.reply_text("تأیید پرداخت فعلاً ممکن نشد؛ دوباره تلاش کن.")
        return
    try:
        amount_value = Decimal(str(verification.get("amount", -1)))
        verified_amount = (
            int(amount_value)
            if amount_value.is_finite() and amount_value == amount_value.to_integral_value()
            else -1
        )
    except (TypeError, ValueError):
        verified_amount = -1
    if (
        verification.get("status") != "paid"
        or str(verification.get("order_id")) != order["order_id"]
        or str(verification.get("payment_id")) != order["payment_id"]
        or verified_amount != int(order["amount_irr"])
    ):
        await message.reply_text("اطلاعات پرداخت با سفارش تطبیق نکرد؛ سفارش تحویل داده نشد.")
        return

    store: OrderStore = context.application.bot_data["store"]
    latest = store.get_by_id(order["order_id"], user.id) or order
    if not isinstance(verification.get("first_verification"), bool):
        await message.reply_text("پاسخ تأیید درگاه ناقص است؛ سفارش تحویل داده نشد.")
        return
    # Only the first successful verification may move an order into fulfillment.
    if latest["status"] == "awaiting_payment":
        if verification["first_verification"] is not True:
            store.update_status(order["order_id"], "payment_review", "PAYMENT_ALREADY_VERIFIED")
            await message.reply_text(
                f"درگاه پرداخت را تأیید کرده، اما ثبت داخلی آن نیاز به بررسی پشتیبانی دارد. کد سفارش: {order['order_id']}"
            )
            for admin_id in settings.admin_ids:
                try:
                    await context.bot.send_message(
                        admin_id,
                        f"پرداخت تأییدشده در درگاه ثبت محلی نشده؛ بررسی لازم است: {order['order_id']}",
                    )
                except Exception:
                    logger.warning("Could not notify configured admin")
            return
        store.mark_paid(order["order_id"])
        latest = store.get_by_id(order["order_id"], user.id) or latest
    if latest["status"] in {"fulfilled", "waiting_supplier", "refund_required", "payment_review", "payment_failed", "payment_expired", "sandbox_paid"}:
        await message.reply_text(_order_summary(latest), reply_markup=_orders_keyboard(latest))
        return
    if latest["status"] not in {"paid", "submitting"}:
        logger.warning("Verified payment could not resume local order: %s", order["order_id"])
        await message.reply_text(
            "پرداخت در درگاه تأیید شده، اما وضعیت سفارش نیاز به بررسی پشتیبانی دارد. "
            f"کد سفارش: {order['order_id']}"
        )
        for admin_id in settings.admin_ids:
            try:
                await context.bot.send_message(
                    admin_id,
                    f"بررسی وضعیت پرداخت لازم است: {order['order_id']}",
                )
            except Exception:
                logger.warning("Could not notify configured admin")
        return
    await _fulfill_order(latest, context, origin_message=message)


async def _notify_customer(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    origin_message: Any | None = None,
) -> None:
    if origin_message:
        await origin_message.reply_text(text, reply_markup=reply_markup)
    else:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)


async def _notify_customer_long(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    origin_message: Any | None = None,
) -> None:
    if origin_message:
        await _reply_long(origin_message, text, reply_markup=reply_markup)
    else:
        while len(text) > 1_800:
            cut = text.rfind("\n", 0, 1_800)
            if cut < 1:
                cut = 1_800
            await context.bot.send_message(chat_id=chat_id, text=text[:cut])
            text = text[cut:].lstrip()
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)


async def _fulfill_order(
    order: dict[str, Any],
    context: ContextTypes.DEFAULT_TYPE,
    origin_message: Any | None = None,
) -> None:
    settings: Settings = context.application.bot_data["settings"]
    store: OrderStore = context.application.bot_data["store"]
    order_id = order["order_id"]
    chat_id = order["chat_id"]
    user_id = order["user_id"]

    if settings.payexa_mode == "sandbox":
        store.update_status(order_id, "sandbox_paid")
        await _notify_customer(
            context,
            chat_id,
            "پرداخت آزمایشی تأیید شد. در حالت آزمایشی، سفارشی از موجودی واقعی تأمین‌کننده ثبت نمی‌شود.",
            origin_message=origin_message,
        )
        return

    if not store.begin_supplier_purchase(order_id):
        latest = store.get_by_id(order_id, user_id)
        if origin_message:
            await origin_message.reply_text(_order_summary(latest or order), reply_markup=_orders_keyboard(latest or order))
        return

    try:
        products = await context.application.bot_data["canboso"].products()
        live_product = next(
            (item for item in products if str(item.get("productId")) == order["product_id"]),
            None,
        )
        live_price = _usd_price(live_product) if live_product else None
        if not live_product or _available(live_product) < int(order["quantity"]):
            raise CanbosoError("SUPPLIER_STOCK_CHANGED")
        if live_price is None or live_price != Decimal(order["supplier_price_usd"]):
            raise CanbosoError("SUPPLIER_PRICE_CHANGED")
        currency, balance = await context.application.bot_data["canboso"].balance()
        if currency != "USD" or balance < live_price:
            raise CanbosoError("SUPPLIER_BALANCE_CHANGED")
        purchase_request = store.purchase_request(order_id)
        result = await context.application.bot_data["canboso"].purchase(order_id, purchase_request)
    except CanbosoError as exc:
        safe_code = re.sub(r"[^A-Z0-9_]", "", exc.code.upper())[:40] or "UPSTREAM_ERROR"
        store.update_status(order_id, "refund_required", safe_code)
        await _notify_customer(
            context,
            chat_id,
            f"پرداخت تأیید شد، اما تأمین سفارش ناموفق بود.\nشمارهٔ سفارش: {order_id}\n"
            "پشتیبانی سفارش را بررسی می‌کند.",
            reply_markup=_main_keyboard(),
            origin_message=origin_message,
        )
        for admin_id in settings.admin_ids:
            try:
                await context.bot.send_message(
                    admin_id,
                    f"پیگیری/بازپرداخت لازم است: {order_id} · {safe_code}",
                )
            except Exception:
                logger.warning("Could not notify configured admin")
        return
    except Exception as exc:
        logger.error("Canboso purchase failed (%s)", type(exc).__name__)
        store.update_status(order_id, "refund_required", "PURCHASE_RESULT_UNKNOWN")
        await _notify_customer(
            context,
            chat_id,
            f"پرداخت تأیید شد، اما نتیجهٔ تأمین نامشخص است.\nشمارهٔ سفارش: {order_id}\n"
            "برای جلوگیری از خرید تکراری، سفارش به پشتیبانی ارجاع شد.",
            reply_markup=_main_keyboard(),
            origin_message=origin_message,
        )
        for admin_id in settings.admin_ids:
            try:
                await context.bot.send_message(
                    admin_id,
                    f"پیگیری/بازپرداخت لازم است: {order_id} · PURCHASE_RESULT_UNKNOWN",
                )
            except Exception:
                logger.warning("Could not notify configured admin")
        return

    supplier_order = result.get("order") if isinstance(result.get("order"), dict) else None
    if not supplier_order or not supplier_order.get("orderCode") or not supplier_order.get("status"):
        store.update_status(order_id, "refund_required", "INVALID_PURCHASE_RESPONSE")
        await _notify_customer(
            context,
            chat_id,
            f"پرداخت تأیید شد، اما پاسخ تأمین‌کننده ناقص بود. کد سفارش: {order_id}\n"
            "پشتیبانی سفارش را بررسی می‌کند.",
            reply_markup=_main_keyboard(),
            origin_message=origin_message,
        )
        for admin_id in settings.admin_ids:
            try:
                await context.bot.send_message(admin_id, f"پاسخ ناقص Canboso: {order_id}")
            except Exception:
                logger.warning("Could not notify configured admin")
        return
    delivery = result.get("delivery") if isinstance(result.get("delivery"), dict) else None
    accounts = delivery.get("accounts") if delivery else None
    if not isinstance(accounts, list) or not any(
        isinstance(account, dict) and (account.get("user") or account.get("password"))
        for account in accounts
    ):
        delivery = None
        accounts = None
    supplier_status = str(supplier_order.get("status") or "")
    outcome, error_code = _supplier_outcome(supplier_order)
    has_account_credentials = isinstance(accounts, list) and any(
        isinstance(account, dict) and (account.get("user") or account.get("password"))
        for account in accounts
    )
    if outcome == "fulfilled" and order["product_type"] == "account" and not has_account_credentials:
        outcome, error_code = "refund_required", "MISSING_DELIVERY_CREDENTIALS"
    if outcome == "refund_required":
        store.save_purchase_result(
            order_id,
            outcome,
            str(supplier_order.get("orderCode") or "") or None,
            supplier_status or None,
            None,
            error_code,
        )
        await _notify_customer(
            context,
            chat_id,
            f"پرداخت تأیید شد، اما تأمین سفارش نیاز به پیگیری دارد. کد سفارش: {order_id}\n"
            "پشتیبانی سفارش را بررسی می‌کند.",
            reply_markup=_main_keyboard(),
            origin_message=origin_message,
        )
        for admin_id in settings.admin_ids:
            try:
                await context.bot.send_message(
                    admin_id,
                    f"پیگیری/بازپرداخت لازم است: {order_id} · {error_code}",
                )
            except Exception:
                logger.warning("Could not notify configured admin")
        return
    local_status = outcome
    store.save_purchase_result(
        order_id,
        local_status,
        str(supplier_order.get("orderCode") or "") or None,
        supplier_status or None,
        delivery,
    )
    latest_order = store.get_by_id(order_id, user_id) or order
    if local_status == "waiting_supplier":
        code = supplier_order.get("orderCode") or order_id
        await _notify_customer(
            context,
            chat_id,
            f"پرداخت تأیید شد و سفارش ثبت شد.\nکد پیگیری: {code}\n"
            "تأمین‌کننده در حال تکمیل سفارش است؛ از بخش سفارش‌های من وضعیت را پیگیری کن.",
            reply_markup=_main_keyboard(),
            origin_message=origin_message,
        )
    elif isinstance(accounts, list) and accounts:
        delivery_text = f"سفارش {order_id} تحویل شد:\n\n{_format_accounts(accounts)}"
        await _notify_customer_long(
            context,
            chat_id,
            delivery_text,
            reply_markup=_orders_keyboard(latest_order),
            origin_message=origin_message,
        )
    else:
        await _notify_customer(
            context,
            chat_id,
            f"سفارش {order_id} با موفقیت ثبت شد.",
            reply_markup=_main_keyboard(),
            origin_message=origin_message,
        )


def _format_accounts(accounts: list[Any]) -> str:
    rendered = []
    for index, account in enumerate(accounts, 1):
        if not isinstance(account, dict):
            continue
        lines = [f"اکانت {index}"]
        labels = {
            "user": "نام کاربری",
            "password": "گذرواژه",
            "verifyEmail": "ایمیل تأیید",
            "expiryText": "اعتبار",
            "otherInfo": "اطلاعات تکمیلی",
        }
        for field, label in labels.items():
            value = account.get(field)
            if value:
                lines.append(f"{label}: {value}")
        rendered.append("\n".join(lines))
    return "\n\n".join(rendered) or "اطلاعات تحویل در دسترس نیست."


async def _reply_long(message: Any, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    while len(text) > 1_800:
        cut = text.rfind("\n", 0, 1_800)
        if cut < 1:
            cut = 1_800
        await message.reply_text(text[:cut])
        text = text[cut:].lstrip()
    await message.reply_text(text, reply_markup=reply_markup)


def _order_summary(order: dict[str, Any]) -> str:
    status = STATUS_LABELS.get(order.get("status", ""), "وضعیت نامشخص")
    return (
        f"{str(order.get('product_name') or 'محصول')[:120]}\n"
        f"سفارش: {order.get('order_id')}\n"
        f"مبلغ: {_toman(int(order.get('amount_irr') or 0))}\n"
        f"وضعیت: {status}"
    )


def _orders_keyboard(order: dict[str, Any]) -> InlineKeyboardMarkup:
    rows = []
    if order.get("status") in {"awaiting_payment", "payment_review"}:
        if order.get("payment_id"):
            rows.append([InlineKeyboardButton("بررسی پرداخت", callback_data=f"verify:{order['order_id']}")])
        else:
            rows.append([InlineKeyboardButton("💳 اطلاعات پرداخت و واریز", callback_data=f"pay_info:{order['order_id']}")])
        rows.append([InlineKeyboardButton("❌ لغو سفارش", callback_data=f"cancel_order:{order['order_id']}")])
    if order.get("status") == "fulfilled" and order.get("encrypted_delivery"):
        rows.append([InlineKeyboardButton("نمایش دوبارهٔ اطلاعات تحویل", callback_data=f"cred:{order['order_id']}")])
    rows.append([InlineKeyboardButton("سفارش‌های من", callback_data="orders")])
    return InlineKeyboardMarkup(rows)


async def _show_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user:
        return
    store: OrderStore = context.application.bot_data["store"]
    orders = store.list_for_user(update.effective_user.id)
    if any(order.get("status") == "waiting_supplier" for order in orders):
        try:
            supplier_orders = await context.application.bot_data["canboso"].orders()
            by_code = {str(item.get("orderCode", "")).casefold(): item for item in supplier_orders}
            for order in orders:
                code = str(order.get("provider_order_code") or "").casefold()
                supplier_order = by_code.get(code) if code else None
                if order.get("status") == "waiting_supplier" and code and not supplier_order:
                    try:
                        exact_orders = await context.application.bot_data["canboso"].orders(
                            str(order["provider_order_code"])
                        )
                    except CanbosoError as exc:
                        if exc.code == "ORDER_NOT_FOUND":
                            continue
                        raise
                    supplier_order = next(
                        (item for item in exact_orders if str(item.get("orderCode", "")).casefold() == code),
                        None,
                    )
                if order.get("status") == "waiting_supplier" and supplier_order:
                    status = str(supplier_order.get("status") or "")
                    fulfillment = str(supplier_order.get("fulfillmentStatus") or "")
                    if status or fulfillment:
                        outcome, error_code = _supplier_outcome(supplier_order)
                        delivery = supplier_order.get("delivery") if isinstance(supplier_order.get("delivery"), dict) else None
                        accounts = delivery.get("accounts") if delivery else None
                        has_credentials = isinstance(accounts, list) and any(
                            isinstance(account, dict) and (account.get("user") or account.get("password"))
                            for account in accounts
                        )
                        if outcome == "fulfilled" and order["product_type"] == "account" and not has_credentials and not order.get("encrypted_delivery"):
                            outcome, error_code = "refund_required", "MISSING_DELIVERY_CREDENTIALS"
                        safe_delivery = delivery if outcome == "fulfilled" and has_credentials else None
                        needs_follow_up = store.update_supplier_status(
                            order["order_id"], status or fulfillment, outcome, error_code, safe_delivery
                        )
                        if needs_follow_up:
                            for admin_id in context.application.bot_data["settings"].admin_ids:
                                try:
                                    await context.bot.send_message(
                                        admin_id,
                                        f"پیگیری/بازپرداخت لازم است: {order['order_id']} · {error_code}",
                                    )
                                except Exception:
                                    logger.warning("Could not notify configured admin")
            orders = store.list_for_user(update.effective_user.id)
        except CanbosoError as exc:
            logger.warning("Canboso order refresh failed: %s", exc.code)
    if not orders:
        text = "هنوز سفارشی ثبت نکرده‌ای."
        markup = _main_keyboard()
    else:
        text = "سفارش‌های اخیرت:\n\n" + "\n\n".join(_order_summary(order) for order in orders)
        rows = []
        for order in orders:
            if order.get("status") == "awaiting_payment":
                if order.get("payment_id"):
                    rows.append(
                        [InlineKeyboardButton(f"بررسی پرداخت {order['order_id']}", callback_data=f"verify:{order['order_id']}")]
                    )
                else:
                    rows.append(
                        [InlineKeyboardButton(f"💳 اطلاعات پرداخت {order['order_id']}", callback_data=f"pay_info:{order['order_id']}")]
                    )
            if order.get("status") == "fulfilled" and order.get("encrypted_delivery"):
                rows.append(
                    [InlineKeyboardButton(f"دریافت اطلاعات {order['order_id']}", callback_data=f"cred:{order['order_id']}")]
                )
        rows.append([InlineKeyboardButton("محصولات", callback_data="catalog")])
        markup = InlineKeyboardMarkup(rows)
    query = update.callback_query
    if query:
        await _safe_edit(query, text, markup)
    elif update.effective_message:
        await update.effective_message.reply_text(text, reply_markup=markup)


async def _send_delivery(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: str) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return
    store: OrderStore = context.application.bot_data["store"]
    order = store.get_by_id(order_id, user.id)
    if not order or order.get("status") != "fulfilled":
        await message.reply_text("اطلاعات تحویل این سفارش هنوز آماده نیست.")
        return
    delivery = store.delivery(order_id, user.id)
    if not delivery:
        await message.reply_text("اطلاعات تحویل برای این سفارش پیدا نشد.")
        return
    accounts = delivery.get("accounts")
    if not isinstance(accounts, list):
        await message.reply_text("اطلاعات تحویل در دسترس نیست.")
        return
    await _reply_long(message, f"اطلاعات تحویل سفارش {order_id}:\n\n{_format_accounts(accounts)}")


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not _is_private(update):
        return
    await query.answer()
    data = query.data or ""
    if data == "cancel":
        draft = context.user_data.pop("draft", None)
        text = "مرحلهٔ خرید لغو شد؛ هنوز سفارشی ساخته نشده است." if draft is not None else "خرید فعالی برای لغو وجود ندارد."
        await _safe_edit(query, text, _main_keyboard())
    elif data == "catalog":
        await _show_catalog_entry(update, context, edit=True)
    elif data.startswith("categories:"):
        # Refresh old category-page buttons by showing the complete category list.
        await _show_catalog_entry(update, context, edit=True)
    elif data == "search:start":
        context.user_data["awaiting_product_search"] = True
        await _safe_edit(query, "نام محصول یا بخشی از توضیحاتش را بفرست تا در محصولات موجود بگردم.", _search_cancel_keyboard())
    elif data == "search:cancel":
        context.user_data.pop("awaiting_product_search", None)
        await _show_catalog_entry(update, context, edit=True)
    elif data == "home":
        await _safe_edit(query, "منوی اصلی", _main_keyboard())
    elif data.startswith("list:"):
        _, product_type, page = data.split(":", 2)
        await _show_catalog(update, context, product_type, int(page), edit=True)
    elif data.startswith("prod:"):
        await _show_product(update, context, data[5:])
    elif data.startswith("buy:"):
        await _begin_checkout(update, context, data[4:])
    elif data.startswith("month:"):
        draft = context.user_data.get("draft")
        try:
            month = int(data.split(":", 1)[1])
            allowed = draft["requirements"].get("allowedMonths") or []
            if month not in [int(value) for value in allowed]:
                raise ValueError
        except (TypeError, ValueError, KeyError):
            await query.message.reply_text("مدت انتخاب‌شده معتبر نیست.")
            return
        draft["purchase"]["slot_months"] = month
        await _create_order_and_payment(update, context)
    elif data.startswith("verify:"):
        order_id = data[7:]
        order = context.application.bot_data["store"].get_by_id(order_id, update.effective_user.id)
        if not order:
            await query.message.reply_text("سفارش پیدا نشد.")
            return
        await _process_verified_order(update, context, order)
    elif data.startswith("cred:"):
        await _send_delivery(update, context, data[5:])
    elif data == "orders":
        await _show_orders(update, context)
    elif data.startswith("pay_info:"):
        order_id = data[len("pay_info:"):]
        store: OrderStore = context.application.bot_data["store"]
        settings: Settings = context.application.bot_data["settings"]
        user_id = update.effective_user.id if update.effective_user else 0
        order = store.get_by_id(order_id, user_id)
        if not order:
            await query.answer("سفارش یافت نشد.", show_alert=True)
            return
        base_irr = (
            _quote_irr(Decimal(order["supplier_price_usd"]), settings)
            if order.get("supplier_price_usd")
            else None
        )
        text, markup = _card_payment_text_and_markup(
            order_id=order_id,
            product_name=order["product_name"],
            base_amount_irr=base_irr,
            amount_irr=int(order["amount_irr"]),
            settings=settings,
        )
        await _safe_edit(query, text, markup, parse_mode="Markdown")
    elif data.startswith("admin_approve:"):
        order_id = data[len("admin_approve:"):]
        settings: Settings = context.application.bot_data["settings"]
        if not update.effective_user or update.effective_user.id not in settings.admin_ids:
            await query.answer("شما دسترسی مدیریت ندارید.", show_alert=True)
            return
        store: OrderStore = context.application.bot_data["store"]
        order = store.get_by_id(order_id)
        if not order:
            await query.answer("سفارش یافت نشد.", show_alert=True)
            return
        if order["status"] not in {"awaiting_payment", "payment_review"}:
            await query.answer("این سفارش قبلاً تعیین تکلیف شده است.", show_alert=True)
            return
        store.mark_paid(order_id)
        latest = store.get_by_id(order_id) or order
        await query.answer("رسید تأیید شد. در حال صدور محصول...")
        await _safe_edit_admin_receipt(query, f"✅ رسید سفارش {order_id} تأیید شد و محصول در حال تحویل است.")
        await _fulfill_order(latest, context, None)
    elif data.startswith("admin_reject:"):
        order_id = data[len("admin_reject:"):]
        settings: Settings = context.application.bot_data["settings"]
        if not update.effective_user or update.effective_user.id not in settings.admin_ids:
            await query.answer("شما دسترسی مدیریت ندارید.", show_alert=True)
            return
        store: OrderStore = context.application.bot_data["store"]
        order = store.get_by_id(order_id)
        if not order:
            await query.answer("سفارش یافت نشد.", show_alert=True)
            return
        if order["status"] not in {"awaiting_payment", "payment_review"}:
            await query.answer("این سفارش قبلاً تعیین تکلیف شده است.", show_alert=True)
            return
        store.update_status(order_id, "payment_failed", "ADMIN_REJECTED")
        await query.answer("رسید رد شد.")
        await _safe_edit_admin_receipt(query, f"❌ رسید سفارش {order_id} توسط شما رد شد.")
        try:
            await context.bot.send_message(
                chat_id=order["chat_id"],
                text=(
                    f"❌ رسید ارسالی برای سفارش {order_id} مورد تأیید قرار نگرفت.\n\n"
                    "علت: عدم تطابق مبلغ واریزی یا عدم واریز به حساب.\n"
                    f"جهت پیگیری می‌توانید با پشتیبانی در ارتباط باشید:\n{SUPPORT_URL}"
                ),
                reply_markup=_main_keyboard(),
            )
        except Exception as exc:
            logger.warning("Could not notify customer about rejected receipt: %s", exc)
    elif data.startswith("cancel_order:"):
        order_id = data[len("cancel_order:"):]
        store: OrderStore = context.application.bot_data["store"]
        user_id = update.effective_user.id if update.effective_user else 0
        order = store.get_by_id(order_id, user_id)
        if not order:
            await query.answer("سفارش یافت نشد.", show_alert=True)
            return
        if order["status"] in {"awaiting_payment", "payment_review"}:
            store.update_status(order_id, "payment_failed", "USER_CANCELLED")
            await query.answer("سفارش لغو شد.")
            await _safe_edit(query, f"سفارش {order_id} لغو شد.", _main_keyboard())
        else:
            await query.answer("این سفارش دیگر قابل لغو نیست.", show_alert=True)


async def _safe_edit_admin_receipt(query: Any, new_text: str) -> None:
    try:
        if getattr(query.message, "caption", None) is not None:
            await query.edit_message_caption(caption=new_text, reply_markup=None)
        elif query.message:
            await query.edit_message_text(text=new_text, reply_markup=None)
    except Exception as exc:
        logger.warning("Could not update admin message (%s)", type(exc).__name__)


async def receipt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update) or not update.effective_user or not update.effective_message:
        return
    user = update.effective_user
    store: OrderStore = context.application.bot_data["store"]
    settings: Settings = context.application.bot_data["settings"]

    order = store.get_active_unpaid_order(user.id)
    if not order:
        await update.effective_message.reply_text(
            "سفارش فعالی در انتظار پرداخت یا ارسال فیش یافت نشد.\n"
            "می‌توانید از منوی محصولات، سفارش جدیدی ثبت کنید.",
            reply_markup=_main_keyboard(),
        )
        return

    order_id = order["order_id"]
    file_id = None
    is_photo = False
    if update.effective_message.photo:
        file_id = update.effective_message.photo[-1].file_id
        is_photo = True
    elif (
        update.effective_message.document
        and update.effective_message.document.mime_type
        and update.effective_message.document.mime_type.startswith("image/")
    ):
        file_id = update.effective_message.document.file_id
        is_photo = False
    else:
        await update.effective_message.reply_text("لطفاً تصویر فیش یا رسید واریز را به صورت عکس ارسال کنید.")
        return

    store.update_status(order_id, "payment_review")
    username_str = f"@{user.username}" if user.username else "بدون نام کاربری"
    amount_str = _toman(int(order["amount_irr"]))

    caption = (
        f"🔔 رسید پرداخت جدید\n\n"
        f"🆔 شناسه سفارش: {order_id}\n"
        f"📦 محصول: {order['product_name']}\n"
        f"💰 مبلغ مورد انتظار: {amount_str}\n"
        f"👤 خریدار: {username_str} ({user.id})\n"
        f"📅 تاریخ: {order.get('created_at', '')[:19].replace('T', ' ')}\n\n"
        "پس از استعلام حساب بانکی، اقدام فرمایید:"
    )
    admin_markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ تأیید و تحویل سفارش", callback_data=f"admin_approve:{order_id}"),
                InlineKeyboardButton("❌ رد رسید", callback_data=f"admin_reject:{order_id}"),
            ]
        ]
    )

    for admin_id in settings.admin_ids:
        try:
            if is_photo and hasattr(context.bot, "send_photo"):
                await context.bot.send_photo(
                    chat_id=admin_id,
                    photo=file_id,
                    caption=caption,
                    reply_markup=admin_markup,
                )
            elif not is_photo and hasattr(context.bot, "send_document"):
                await context.bot.send_document(
                    chat_id=admin_id,
                    document=file_id,
                    caption=caption,
                    reply_markup=admin_markup,
                )
            else:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=caption,
                    reply_markup=admin_markup,
                )
        except Exception as exc:
            logger.warning("Could not send receipt to admin %s: %s", admin_id, exc)

    await update.effective_message.reply_text(
        f"✅ رسید شما برای سفارش {order_id} دریافت شد.\n\n"
        "رسید در صف بررسی توسط پشتیبانی قرار گرفت. به محض تأیید واریز، سفارش به صورت خودکار صادر و اطلاعات آن ارسال خواهد شد.\n"
        "می‌توانید وضعیت سفارش را از بخش «سفارش‌های من» نیز مشاهده کنید.",
        reply_markup=_orders_keyboard(order),
    )


async def _safe_edit(
    query: Any,
    text: str,
    markup: InlineKeyboardMarkup,
    parse_mode: str | None = None,
) -> None:
    try:
        if parse_mode is not None:
            await query.edit_message_text(text, reply_markup=markup, parse_mode=parse_mode)
        else:
            await query.edit_message_text(text, reply_markup=markup)
    except BadRequest as exc:
        if "Message is not modified" not in str(exc):
            logger.warning("Telegram edit failed (%s)", type(exc).__name__)


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled update error (%s)", type(context.error).__name__)


def build_application(settings: Settings) -> Application:
    async def initialize(app: Application) -> None:
        bot = await app.bot.get_me()
        app.bot_data["bot_username"] = settings.telegram_username or bot.username
        await app.bot.set_my_commands(
            [
                ("start", "منوی اصلی"),
                ("products", "دیدن محصولات"),
                ("orders", "سفارش‌های من"),
                ("help", "راهنمای خرید"),
                ("cancel", "لغو مرحلهٔ خرید"),
            ]
        )

    application = (
        ApplicationBuilder()
        .token(settings.telegram_token)
        .concurrent_updates(1)
        .post_init(initialize)
        .build()
    )
    application.bot_data["settings"] = settings
    application.bot_data["store"] = OrderStore(settings.database_path, settings.encryption_key)
    application.bot_data["canboso"] = CanbosoClient(settings.canboso_key)
    application.bot_data["payexa"] = (
        PayexaClient(settings.payexa_key, settings.payexa_base_url) if settings.payexa_key else None
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("products", products_command))
    application.add_handler(CommandHandler("orders", _show_orders))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CallbackQueryHandler(callback))
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, text_router))
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & (filters.PHOTO | filters.Document.IMAGE),
            receipt_handler,
        )
    )
    application.add_error_handler(_on_error)
    return application
