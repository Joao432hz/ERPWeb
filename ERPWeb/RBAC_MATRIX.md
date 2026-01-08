# RBAC Matrix — ERPWeb (C2)

## Fuente de verdad
ERPWeb utiliza un RBAC propio (custom) basado en:
- UserRole (usuario → roles)
- RolePermission (rol → permisos)
- Permission (código único, ej: `purchases.order.receive`)

La autorización se aplica mediante el decorator `require_permission(perm_code)`.

## Comportamiento del decorator
1) Si el usuario NO está autenticado → 401
2) Si es superuser → bypass (acceso total)
3) Si tiene el permiso por RBAC custom → OK
4) Fallback opcional a permisos Django SOLO si el perm_code está mapeado en `DJANGO_PERM_FALLBACK_MAP`
5) Si no cumple → 403

> Recomendación de producto: usar fallback SOLO para permisos de lectura (view) si aporta valor.
> Acciones críticas (pay/receive/confirm) deben depender SOLO del RBAC custom.

---

## Roles funcionales (C2)

### Admin
- Acceso total por ser superuser (bypass en decorator).
- Para operación diaria se recomienda también tener roles custom, pero el bypass existe para soporte.

### Compras
Objetivo: gestionar órdenes de compra (DRAFT) y confirmar.
NO puede recibir.

Permisos:
- purchases.supplier.view
- purchases.order.view
- purchases.order.create
- purchases.order.edit
- purchases.order.confirm
- purchases.order.cancel
- stock.product.view

### Depósito
Objetivo: recibir compras e impactar stock.
NO puede confirmar ni editar PO.

Permisos:
- purchases.order.view
- purchases.order.receive
- stock.product.view
- stock.movement.view
- stock.movement.create

---

## Regla ERP (C2)
- Compras puede CONFIRMAR pero no puede RECIBIR.
- Depósito puede RECIBIR pero no puede CONFIRMAR.
- Recibir es una acción crítica porque impacta stock y finanzas.
