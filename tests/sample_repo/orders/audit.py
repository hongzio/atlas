"""Audit helpers. Exercises decorators and non-call symbol usage for the indexer."""

from orders.models import Order
from orders.repository import OrderRepository


def logged(fn):
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)
    return wrapper


def null_sink(entry: str) -> None:
    pass


@logged
def record_audit(repo: OrderRepository, order: Order, sink=null_sink) -> str:
    entry = f"audit {order.idempotency_key}"
    sink(entry)
    return entry


DEFAULT_SINKS = [null_sink]


def shadow_case() -> str:
    logged = "not the decorator"
    return logged
