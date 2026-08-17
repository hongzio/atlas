from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4


class OrderStatus(Enum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"


@dataclass
class Order:
    customer_id: str
    amount_cents: int
    status: OrderStatus = OrderStatus.PENDING
    idempotency_key: str = field(default_factory=lambda: str(uuid4()))

    def mark_paid(self) -> None:
        self.status = OrderStatus.PAID

    def mark_failed(self) -> None:
        self.status = OrderStatus.FAILED
