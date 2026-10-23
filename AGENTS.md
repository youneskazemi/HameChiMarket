# HameChiMarket agent notes

- پیش از پرسش معماری، `graphify-out/graph.json` را با `graphify query` بررسی کن؛ یادداشت‌های قابل مرور در `graphify-out/obsidian/` هستند. این‌ها خروجی تولیدشده‌اند، نه منبع اصلی.
- منطق بات در `src/hamechi_market/bot.py`، یکپارچه‌سازی‌ها در `canboso.py` و `payexa.py`، تنظیمات در `config.py` و سفارش‌های محلی در `storage.py` است.
- کلیدها فقط در `.env` محلی می‌مانند. برای سفارش واقعی، پرداخت PAYEXA را سمت سرور تأیید کن و خرید Canboso را با همان `Idempotency-Key` سفارش تکرارپذیر نگه دار.
- پیش از تغییر قرارداد درخواست‌ها، Swagger زندهٔ Buyer API در `https://canboso.com/api/swagger/` و مستندات پذیرندهٔ PAYEXA را بخوان.
- برای مستندات متغیر کتابخانه‌ها و فریم‌ورک‌ها از Context7 استفاده کن: ابتدا `resolve-library-id` و سپس `query-docs`، با نسخهٔ منطبق با وابستگی‌های پروژه.
