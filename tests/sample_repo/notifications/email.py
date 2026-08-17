def send_receipt(customer_id: str, receipt_id: str) -> None:
    format_receipt(customer_id, receipt_id)


def format_receipt(customer_id: str, receipt_id: str) -> str:
    return f"receipt {receipt_id} for {customer_id}"
