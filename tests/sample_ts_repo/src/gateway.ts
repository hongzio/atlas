export class ChargeFailedError extends Error {}

export function chargeCustomer(
  customerId: string,
  amountCents: number,
  idempotencyKey: string,
): string {
  if (amountCents <= 0) {
    throw new ChargeFailedError("amount must be positive");
  }
  return `rcpt_${idempotencyKey.slice(0, 8)}`;
}
