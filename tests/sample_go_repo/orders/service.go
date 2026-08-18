package orders

import "fmt"

type OrderService struct {
	repo *OrderRepository
}

func NewService(repo *OrderRepository) *OrderService {
	return &OrderService{repo: repo}
}

func (s *OrderService) Create(customerID string, amountCents int, idempotencyKey string) (*Order, error) {
	existing := s.repo.FindByKey(idempotencyKey)
	if existing != nil {
		return nil, fmt.Errorf("duplicate order: %s", idempotencyKey)
	}

	order := &Order{
		CustomerID:     customerID,
		AmountCents:    amountCents,
		IdempotencyKey: idempotencyKey,
		Status:         StatusPending,
	}
	s.repo.Save(order)

	if amountCents <= 0 {
		order.MarkFailed()
		s.repo.Save(order)
		return nil, fmt.Errorf("amount must be positive")
	}

	order.MarkPaid()
	s.repo.Save(order)
	return order, nil
}
