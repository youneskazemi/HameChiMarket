from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from cryptography.fernet import Fernet


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class OrderStore:
    def __init__(self, path: Path, encryption_key: bytes | None) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.cipher = Fernet(encryption_key) if encryption_key else None
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level="IMMEDIATE")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self._connection() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    product_id TEXT NOT NULL,
                    product_name TEXT NOT NULL,
                    product_type TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    amount_irr INTEGER NOT NULL,
                    supplier_price_usd TEXT NOT NULL,
                    payment_id TEXT UNIQUE,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    encrypted_request BLOB NOT NULL,
                    encrypted_delivery BLOB,
                    provider_order_code TEXT,
                    provider_order_status TEXT,
                    error_code TEXT
                )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS orders_user_created ON orders(user_id, created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS orders_payment_id ON orders(payment_id)")

    def _encrypt(self, value: dict[str, Any]) -> bytes:
        if not self.cipher:
            raise RuntimeError("Delivery encryption is not configured")
        return self.cipher.encrypt(json.dumps(value, ensure_ascii=False).encode("utf-8"))

    def _decrypt(self, value: bytes) -> dict[str, Any]:
        if not self.cipher:
            raise RuntimeError("Delivery encryption is not configured")
        return json.loads(self.cipher.decrypt(value).decode("utf-8"))

    def create_order(self, order: dict[str, Any], purchase_request: dict[str, Any]) -> None:
        encrypted_request = self._encrypt(purchase_request)
        now = utc_now()
        with self._connection() as conn:
            conn.execute(
                """INSERT INTO orders (
                    order_id, user_id, chat_id, product_id, product_name, product_type,
                    quantity, amount_irr, supplier_price_usd, status, created_at,
                    updated_at, encrypted_request
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'awaiting_payment', ?, ?, ?)""",
                (
                    order["order_id"], order["user_id"], order["chat_id"], order["product_id"],
                    order["product_name"], order["product_type"], order["quantity"],
                    order["amount_irr"], order["supplier_price_usd"], now, now, encrypted_request,
                ),
            )

    def attach_payment(self, order_id: str, payment_id: str) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE orders SET payment_id = ?, updated_at = ? WHERE order_id = ? AND status = 'awaiting_payment'",
                (payment_id, utc_now(), order_id),
            )

    def update_status(self, order_id: str, status: str, error_code: str | None = None) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE orders SET status = ?, error_code = ?, updated_at = ? WHERE order_id = ?",
                (status, error_code, utc_now(), order_id),
            )

    def update_supplier_status(
        self,
        order_id: str,
        provider_status: str,
        status: str,
        error_code: str | None = None,
        delivery: dict[str, Any] | None = None,
    ) -> bool:
        encrypted_delivery = self._encrypt(delivery) if delivery else None
        with self._connection() as conn:
            cursor = conn.execute(
                "UPDATE orders SET provider_order_status = ?, status = ?, error_code = ?, "
                "encrypted_delivery = COALESCE(?, encrypted_delivery), updated_at = ? "
                "WHERE order_id = ? AND status = 'waiting_supplier'",
                (provider_status, status, error_code, encrypted_delivery, utc_now(), order_id),
            )
        return cursor.rowcount == 1 and status == "refund_required"

    def mark_paid(self, order_id: str) -> bool:
        with self._connection() as conn:
            cursor = conn.execute(
                "UPDATE orders SET status = 'paid', error_code = NULL, updated_at = ? "
                "WHERE order_id = ? AND status IN ('awaiting_payment', 'payment_review')",
                (utc_now(), order_id),
            )
        return cursor.rowcount == 1

    def get_active_unpaid_order(self, user_id: int) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM orders WHERE user_id = ? AND status IN ('awaiting_payment', 'payment_review') "
                "ORDER BY created_at DESC LIMIT 1",
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def begin_supplier_purchase(self, order_id: str) -> bool:
        with self._connection() as conn:
            row = conn.execute("SELECT status FROM orders WHERE order_id = ?", (order_id,)).fetchone()
            if not row or row["status"] not in {"paid", "submitting"}:
                return False
            conn.execute(
                "UPDATE orders SET status = 'submitting', updated_at = ? WHERE order_id = ?",
                (utc_now(), order_id),
            )
        return True

    def get_by_id(self, order_id: str, user_id: int | None = None) -> dict[str, Any] | None:
        with self._connection() as conn:
            if user_id is None:
                row = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM orders WHERE order_id = ? AND user_id = ?", (order_id, user_id)
                ).fetchone()
        return dict(row) if row else None

    def get_by_payment(self, payment_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM orders WHERE payment_id = ?", (payment_id,)).fetchone()
        return dict(row) if row else None

    def list_for_user(self, user_id: int, limit: int = 10) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def purchase_request(self, order_id: str) -> dict[str, Any]:
        order = self.get_by_id(order_id)
        if not order:
            raise KeyError(order_id)
        return self._decrypt(order["encrypted_request"])

    def save_purchase_result(
        self,
        order_id: str,
        status: str,
        provider_order_code: str | None,
        provider_order_status: str | None,
        delivery: dict[str, Any] | None,
        error_code: str | None = None,
    ) -> None:
        encrypted_delivery = self._encrypt(delivery) if delivery else None
        with self._connection() as conn:
            conn.execute(
                """UPDATE orders SET status = ?, provider_order_code = ?,
                    provider_order_status = ?, encrypted_delivery = ?, error_code = ?,
                    updated_at = ? WHERE order_id = ?""",
                (status, provider_order_code, provider_order_status, encrypted_delivery, error_code, utc_now(), order_id),
            )

    def delivery(self, order_id: str, user_id: int) -> dict[str, Any] | None:
        order = self.get_by_id(order_id, user_id)
        if not order or not order["encrypted_delivery"]:
            return None
        return self._decrypt(order["encrypted_delivery"])
