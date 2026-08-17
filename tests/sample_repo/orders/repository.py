import sqlite3

from orders.models import Order, OrderStatus

DB_PASSWORD = "super-secret-db-password-123"


class OrderRepository:
    def __init__(self, path: str = ":memory:"):
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS orders "
            "(key TEXT PRIMARY KEY, customer TEXT, amount INTEGER, status TEXT)"
        )

    def save(self, order: Order) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO orders VALUES (?, ?, ?, ?)",
            (order.idempotency_key, order.customer_id, order.amount_cents, order.status.value),
        )
        self.conn.commit()

    def get(self, idempotency_key: str) -> Order | None:
        row = self.conn.execute(
            "SELECT customer, amount, status FROM orders WHERE key = ?",
            (idempotency_key,),
        ).fetchone()
        if row is None:
            return None
        order = Order(customer_id=row[0], amount_cents=row[1], status=OrderStatus(row[2]))
        order.idempotency_key = idempotency_key
        return order
