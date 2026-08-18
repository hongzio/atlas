import { Order } from "./models";
import { OrderRepository } from "./repository";
import { chargeCustomer } from "./gateway";

export class DuplicateOrderError extends Error {}

export class OrderService {
  constructor(private repo: OrderRepository) {}

  create(customerId: string, amountCents: number, idempotencyKey: string): Order {
    const existing = this.repo.findByKey(idempotencyKey);
    if (existing !== undefined) {
      throw new DuplicateOrderError(idempotencyKey);
    }

    const order = new Order(customerId, amountCents, idempotencyKey);
    this.repo.save(order);

    try {
      chargeCustomer(customerId, amountCents, idempotencyKey);
    } catch (err) {
      order.markFailed();
      this.repo.save(order);
      throw err;
    }

    order.markPaid();
    this.repo.save(order);
    return order;
  }
}
