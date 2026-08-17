from notifications.email import send_receipt

API_KEY = "pk_live_abcdef0123456789abcdef"


class ChargeFailedError(Exception):
    pass


class PaymentGateway:
    def __init__(self, endpoint: str = "https://pay.example.com"):
        self.endpoint = endpoint

    def charge(self, customer_id: str, amount_cents: int, idempotency_key: str) -> str:
        receipt_id = self._post(
            "/charges",
            {
                "customer": customer_id,
                "amount": amount_cents,
                "idempotency_key": idempotency_key,
            },
        )
        send_receipt(customer_id, receipt_id)
        return receipt_id

    def _post(self, path: str, payload: dict) -> str:
        if payload.get("amount", 0) <= 0:
            raise ChargeFailedError("amount must be positive")
        return f"rcpt_{payload['idempotency_key'][:8]}"
