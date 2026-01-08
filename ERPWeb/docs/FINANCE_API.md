# Finance Module — API & BI Contract (MVP)

## Objetivo
El módulo Finance registra movimientos financieros creados automáticamente por eventos del ERP:

- Purchase RECEIVED → PAYABLE (cuenta a pagar)
- Sale CONFIRMED → RECEIVABLE (cuenta a cobrar)

Este contrato está pensado para consumo por herramientas externas (Power BI / Excel / ETL), con endpoints estables y filtros consistentes.

---

## Endpoints oficiales

### 1) Listado paginado (JSON)
GET `/finance/movements/`

#### Filtros (query params)
- `status`: `OPEN` | `PAID`
- `movement_type`: `PAYABLE` | `RECEIVABLE`
- `source_type`: `PURCHASE` | `SALE`
- `from`: `YYYY-MM-DD` o ISO datetime (filtra por created_at >= from)
- `to`: `YYYY-MM-DD` o ISO datetime (filtra por created_at <= to)

#### Paginación
- `page` (default 1)
- `page_size` (default 50, max 500)

#### Orden (ordering)
Permitidos:
- `created_at`, `-created_at`
- `paid_at`, `-paid_at`
- `amount`, `-amount`
- `id`, `-id`

#### Respuesta (schema)
```json
{
  "status": "ok",
  "count": 0,
  "page": 1,
  "page_size": 50,
  "ordering": "-created_at",
  "results": [
    {
      "id": 1,
      "movement_type": "PAYABLE",
      "source_type": "PURCHASE",
      "source_id": 4,
      "amount": "0.00",
      "status": "OPEN",
      "notes": "",
      "created_at": "2026-01-06T17:00:00+00:00",
      "paid_at": null
    }
  ]
}
