# InventorPro Booking & POS Plan

## Core Rules

1. **Stock is consumed by services** — each service uses products from inventory. When a service is done, stock is deducted automatically.
2. **Clients book packages or services only** — no products in the booking flow.
3. **Products are add-ons at POS** — when the client comes to the shop, the owner can add products as add-ons during checkout.

## Booking Flow

```
Client registers → Admin approves → Client logs in
  → Client picks a Package OR a Service (not both)
  → Client picks a date and available worker slot
  → Booking is created (status: pending)
  → Admin confirms booking
  → Transaction number generated
  → Client shows transaction number to owner
  → Owner opens POS
  → POS shows service/package total
  → Owner can ADD PRODUCTS as add-ons
  → Choose payment: Cash or GCash
  → Receipt generated
  → Stock deducted for service consumption + add-on products
```

## Walk-In Flow

```
Owner creates walk-in appointment (today only)
  → Owner selects or creates walk-in client
  → Owner picks service or package
  → Transaction number generated
  → POS opens
  → Owner can add products as add-ons
  → Payment + receipt
```

## Database Changes Needed

### `service_products` (NEW)
Links products consumed by a service:
- `id`, `service_id`, `product_id`, `quantity_used`

### `appointments` (NEW)
- `id`, `client_id`, `worker_id`, `service_id`, `package_id`
- `appointment_date`, `start_time`, `end_time`
- `status`: pending, confirmed, completed, cancelled
- `transaction_number`
- `total_amount`, `payment_status`, `payment_method`
- `is_walkin` (boolean)
- `created_at`

### `appointment_addons` (NEW)
Products added at POS:
- `id`, `appointment_id`, `product_id`, `quantity`, `price`

### `receipts` (NEW)
- `id`, `appointment_id`, `transaction_number`, `total`, `payment_method`, `reference`, `created_at`

## Phases

1. **Service-Product linking** — admin defines which products each service consumes
2. **Client booking** — client picks package or service, date, worker slot
3. **Admin appointment management** — confirm, reject, view pending
4. **Walk-in** — owner creates today-only appointment
5. **POS** — service/package total + product add-ons + payment
6. **Receipt** — printable/email receipt
7. **Stock deduction** — auto-deduct on completion

## Approval

Reply **"proceed"** to start Phase 1 (service-product linking).
