import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
SAMPLE_REPO = ROOT / "tests" / "sample_repo"


def run_script(name: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name), *args],
        capture_output=True, text=True,
    )


@pytest.fixture(scope="session")
def index_output(tmp_path_factory) -> dict:
    out = tmp_path_factory.mktemp("idx") / "index.json"
    proc = run_script(
        "atlas_index.py",
        "--repo", str(SAMPLE_REPO),
        "--entry", "orders/service.py",
        "--hops", "2",
        "--out", str(out),
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(out.read_text())


def make_artifact(idx: dict, artifact_id: str = "art_sample_orders") -> dict:
    """Build a valid artifact from the index output plus a canonical narrative."""
    symbols = {s["symbol_id"]: s for s in idx["index"]["symbols"]}

    def ev(sid: str) -> dict:
        s = symbols[sid]
        return {
            "symbol_id": sid,
            "path": s["path"],
            "range": s["range"],
            "content_hash": s["content_hash"],
        }

    create = "python:orders.service:OrderService.create"
    charge = "python:payments.gateway:PaymentGateway.charge"
    save = "python:orders.repository:OrderRepository.save"
    get = "python:orders.repository:OrderRepository.get"

    return {
        "schema_version": "2.0",
        "artifact_id": artifact_id,
        "type": "learning",
        "title": "orders subsystem onboarding",
        "repository": {k: v for k, v in idx["repository"].items() if k != "root"},
        "slice": idx["slice"],
        "inputs": {"skill_version": "0.2.0", "schema_version": "2.0"},
        "index": idx["index"],
        "overview": {
            "summary": (
                "The orders subsystem turns a customer request into a persisted, "
                "paid order. One question drives its design: what happens when the "
                "same request arrives twice? A network retry can deliver the same "
                "create call two times. Without protection, the customer pays "
                "twice. The answer is an idempotency key. OrderService.create "
                "checks that key before it saves or charges anything.\n\n"
                "Start reading at OrderService.create in orders/service.py. Every "
                "other module serves that one method. The diagram below follows "
                "one example request, cus_1442 paying 8900 cents under key "
                "idem_7f3a, on its way through the system."
            ),
            "architecture": {
                "modules": [
                    {"id": "orders.service", "label": "OrderService",
                     "path": "orders/service.py",
                     "description": "Entry point, owns the create flow"},
                    {"id": "orders.repository", "label": "OrderRepository",
                     "path": "orders/repository.py",
                     "description": "SQLite persistence for orders"},
                    {"id": "payments.gateway", "label": "PaymentGateway",
                     "path": "payments/gateway.py",
                     "description": "Charges the customer"},
                    {"id": "notifications.email", "label": "notifications.email",
                     "path": "notifications/email.py",
                     "description": "Sends the receipt"},
                ],
                "edges": [
                    {"from": "orders.service", "to": "orders.repository",
                     "kind": "calls",
                     "label": "get(key) / save(Order)",
                     "example": 'save(Order(customer_id="cus_1442", amount_cents=8900, '
                                'status=PENDING, idempotency_key="idem_7f3a"))'},
                    {"from": "orders.service", "to": "payments.gateway",
                     "kind": "calls",
                     "label": "charge(customer_id, amount_cents, idempotency_key)",
                     "example": 'charge("cus_1442", 8900, "idem_7f3a") -> "rcpt_idem_7f3"'},
                    {"from": "payments.gateway", "to": "notifications.email",
                     "kind": "calls",
                     "label": "send_receipt(customer_id, receipt_id)",
                     "example": 'send_receipt("cus_1442", "rcpt_idem_7f3")'},
                ],
            },
        },
        "flows": [
            {
                "id": "flow-create-order",
                "title": "How an order is created",
                "summary": (
                    "OrderService.create moves one request through three stages: "
                    "reject duplicates, persist, then charge. Follow the example "
                    "request cus_1442 / 8900 / idem_7f3a through each stage."
                ),
                "steps": [
                    {
                        "title": "Reject duplicates before any work",
                        "detail": (
                            "The flow starts with the duplicate check. create calls "
                            "repo.get(idempotency_key) before it creates anything. If an "
                            "order already exists under the same key, create raises "
                            "DuplicateOrderError and stops. This is what makes retries "
                            "safe. The key idem_7f3a maps to at most one order, no matter "
                            "how many times the request arrives. Only a fresh key reaches "
                            "the next stage, where the order is written to storage."
                        ),
                        "branches": [
                            {
                                "condition": "existing is not None",
                                "outcome": "create raises DuplicateOrderError. No order is created.",
                            }
                        ],
                        "evidence": [ev(create), ev(get)],
                    },
                    {
                        "title": "Persist first, then charge",
                        "detail": (
                            "With a fresh key, create builds an Order in PENDING state "
                            "and saves it through repo.save before any money moves. The "
                            "order of these two steps matters. If the process crashes "
                            "during payment, the PENDING row is the evidence that a "
                            "charge may have started. Then create calls "
                            "gateway.charge(\"cus_1442\", 8900, \"idem_7f3a\"). On "
                            "success, create marks the order PAID and saves it again. "
                            "The gateway sends receipt rcpt_idem_7f3 to the customer by "
                            "email."
                        ),
                        "error_path": (
                            "ChargeFailedError: create marks the order FAILED, saves it, "
                            "and re-raises to the caller."
                        ),
                        "evidence": [ev(create), ev(charge), ev(save)],
                    },
                ],
            }
        ],
        "concepts": [
            {
                "id": "concept-idempotency-key",
                "name": "idempotency_key",
                "definition": (
                    "A caller-supplied key that makes create safe to retry. A second "
                    "create call with the same key does not create a second order. It "
                    "raises DuplicateOrderError instead."
                ),
                "contrast_with": (
                    "Order's default uuid4 key is server-generated, so each retry "
                    "would get a new key and idempotency would be lost"
                ),
                "evidence": [ev(create)],
            }
        ],
        "invariants": [
            {
                "id": "inv-idempotency",
                "statement": "The same idempotency_key never creates two orders",
                "rationale": (
                    "create reads the key before it writes, so a duplicate request "
                    "stops at the guard clause"
                ),
                "status": "proposed",
                "evidence": [ev(create), ev(get)],
            }
        ],
        "lessons": {
            "predict": [
                {
                    "question": (
                        "gateway.charge raises after repo.save has written the PENDING "
                        "order. What state is the order left in, and what does the "
                        "caller see?"
                    ),
                    "reveal": (
                        "The order is saved in FAILED state and the exception "
                        "propagates to the caller. The record survives, so a retry "
                        "with the same key is rejected as a duplicate."
                    ),
                    "evidence": [ev(create)],
                }
            ],
            "explain_back": [
                {
                    "prompt": (
                        "Explain why create saves the order before it charges the "
                        "customer, and what would break if the two steps were swapped."
                    ),
                    "rubric": [
                        "A crash between charge and save would lose the record of a started charge",
                        "The PENDING row marks a possibly-started charge",
                    ],
                }
            ],
            "localization": [
                {
                    "task": (
                        "Receipts must go out by SMS instead of email. Where do you "
                        "make the change?"
                    ),
                    "answer": (
                        "In payments/gateway.py. PaymentGateway.charge calls "
                        "send_receipt after a successful charge. Replace that call."
                    ),
                    "evidence": [ev(charge)],
                }
            ],
        },
        "unknowns": [
            "PaymentGateway._post is a stub, not a real HTTP call. "
            "Production failure modes are unverified."
        ],
    }
