export enum OrderStatus {
  Pending = "pending",
  Paid = "paid",
  Failed = "failed",
}

export class Order {
  status: OrderStatus = OrderStatus.Pending;

  constructor(
    public customerId: string,
    public amountCents: number,
    public idempotencyKey: string,
  ) {}

  markPaid(): void {
    this.status = OrderStatus.Paid;
  }

  markFailed(): void {
    this.status = OrderStatus.Failed;
  }
}
