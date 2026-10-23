from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import load_dotenv


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    telegram_username: str
    canboso_key: str
    payexa_key: str
    payexa_mode: str
    payexa_base_url: str
    usd_to_irr_rate: Decimal | None
    seller_margin_bps: int | None
    encryption_key: bytes | None
    database_path: Path
    admin_ids: frozenset[int]
    payment_method: str = "payexa"
    card_number: str = ""
    card_holder_name: str = ""

    @property
    def checkout_ready(self) -> bool:
        gateway_ready = (
            bool(self.card_number and self.card_holder_name)
            if self.payment_method == "card"
            else bool(self.payexa_key)
        )
        return bool(
            gateway_ready
            and self.usd_to_irr_rate
            and self.seller_margin_bps is not None
            and self.encryption_key
            and self.admin_ids
        )


def load_settings() -> Settings:
    load_dotenv()
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    canboso_key = os.getenv("CANBOSO_API_KEY", "").strip()
    if not telegram_token:
        raise ConfigurationError("TELEGRAM_BOT_TOKEN is required")
    if not canboso_key:
        raise ConfigurationError("CANBOSO_API_KEY is required")

    mode = os.getenv("PAYEXA_MODE", "sandbox").strip().lower()
    if mode not in {"sandbox", "live"}:
        raise ConfigurationError("PAYEXA_MODE must be sandbox or live")
    payexa_host = "https://sandbox.pexn.ir" if mode == "sandbox" else "https://pay.pexn.ir"

    rate_text = os.getenv("USD_TO_IRR_RATE", "").strip()
    try:
        rate = Decimal(rate_text) if rate_text else None
    except InvalidOperation as exc:
        raise ConfigurationError("USD_TO_IRR_RATE must be a positive number") from exc
    if rate is not None and (not rate.is_finite() or rate <= 0):
        raise ConfigurationError("USD_TO_IRR_RATE must be a positive number")

    margin_text = os.getenv("SELLER_MARGIN_BPS", "").strip()
    try:
        margin = int(margin_text) if margin_text else None
    except ValueError as exc:
        raise ConfigurationError("SELLER_MARGIN_BPS must be a non-negative integer") from exc
    if margin is not None and not 0 <= margin <= 100_000:
        raise ConfigurationError("SELLER_MARGIN_BPS must be between 0 and 100000")

    encryption_text = os.getenv("DELIVERY_ENCRYPTION_KEY", "").strip()
    try:
        encryption_key = encryption_text.encode("ascii") if encryption_text else None
    except UnicodeEncodeError as exc:
        raise ConfigurationError("DELIVERY_ENCRYPTION_KEY must be a valid Fernet key") from exc
    if encryption_key:
        try:
            Fernet(encryption_key)
        except (ValueError, TypeError) as exc:
            raise ConfigurationError("DELIVERY_ENCRYPTION_KEY is invalid") from exc

    admin_ids: set[int] = set()
    for item in os.getenv("ADMIN_TELEGRAM_IDS", "").split(","):
        item = item.strip()
        if item:
            try:
                admin_ids.add(int(item))
            except ValueError as exc:
                raise ConfigurationError("ADMIN_TELEGRAM_IDS must contain numeric IDs") from exc

    card_number = os.getenv("CARD_NUMBER", "").strip().replace(" ", "").replace("-", "")
    card_holder_name = os.getenv("CARD_HOLDER_NAME", "").strip()

    payment_method_env = os.getenv("PAYMENT_METHOD", "").strip().lower()
    if payment_method_env:
        if payment_method_env not in {"card", "payexa"}:
            raise ConfigurationError("PAYMENT_METHOD must be 'card' or 'payexa'")
        payment_method = payment_method_env
    else:
        payment_method = "card" if (card_number or not os.getenv("PAYEXA_API_KEY")) else "payexa"

    return Settings(
        telegram_token=telegram_token,
        telegram_username=os.getenv("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@"),
        canboso_key=canboso_key,
        payexa_key=os.getenv("PAYEXA_API_KEY", "").strip(),
        payexa_mode=mode,
        payexa_base_url=f"{payexa_host}/v2/payments",
        usd_to_irr_rate=rate,
        seller_margin_bps=margin,
        encryption_key=encryption_key,
        database_path=Path(os.getenv("DATABASE_PATH", "data/orders.sqlite3")),
        admin_ids=frozenset(admin_ids),
        payment_method=payment_method,
        card_number=card_number,
        card_holder_name=card_holder_name,
    )
