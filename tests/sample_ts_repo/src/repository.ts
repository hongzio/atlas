import { Order } from "./models";

export class OrderRepository {
  private orders = new Map<string, Order>();

  save(order: Order): void {
    this.orders.set(order.idempotencyKey, order);
  }

  findByKey(idempotencyKey: string): Order | undefined {
    return this.orders.get(idempotencyKey);
  }
}
