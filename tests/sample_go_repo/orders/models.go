package orders

type OrderStatus string

const (
	StatusPending OrderStatus = "pending"
	StatusPaid    OrderStatus = "paid"
	StatusFailed  OrderStatus = "failed"
)

type Order struct {
	CustomerID     string
	AmountCents    int
	IdempotencyKey string
	Status         OrderStatus
}

func (o *Order) MarkPaid() {
	o.Status = StatusPaid
}

func (o *Order) MarkFailed() {
	o.Status = StatusFailed
}
