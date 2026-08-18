package orders

type OrderRepository struct {
	orders map[string]*Order
}

func NewRepository() *OrderRepository {
	return &OrderRepository{orders: map[string]*Order{}}
}

func (r *OrderRepository) Save(order *Order) {
	r.orders[order.IdempotencyKey] = order
}

func (r *OrderRepository) FindByKey(idempotencyKey string) *Order {
	return r.orders[idempotencyKey]
}
