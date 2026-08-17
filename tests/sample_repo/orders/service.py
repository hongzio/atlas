from orders.models import Order
from orders.repository import OrderRepository
from payments.gateway import PaymentGateway


class DuplicateOrderError(Exception):
    pass


class OrderService:
    """Entry point for order creation. Prevents duplicates via idempotency_key."""

    def __init__(self, repo: OrderRepository, gateway: PaymentGateway):
        self.repo = repo
        self.gateway = gateway

    def create(self, customer_id: str, amount_cents: int, idempotency_key: str) -> Order:
        existing = self.repo.get(idempotency_key)
        if existing is not None:
            raise DuplicateOrderError(idempotency_key)

        order = Order(customer_id=customer_id, amount_cents=amount_cents)
        order.idempotency_key = idempotency_key
        self.repo.save(order)

        try:
            self.gateway.charge(customer_id, amount_cents, idempotency_key)
        except Exception:
            order.mark_failed()
            self.repo.save(order)
            raise

        order.mark_paid()
        self.repo.save(order)
        return order
