# ERPWeb — Reglas de Seguridad y Uso de APIs

## Propósito

Este documento define las reglas oficiales de seguridad, autenticación,
autorización y experiencia de uso de las APIs de ERPWeb.

Su objetivo es garantizar:
- consistencia técnica
- escalabilidad
- seguridad operativa
- integraciones seguras
- y una experiencia estable para usuarios y sistemas

ERPWeb está diseñado como un **ERP transaccional profesional y vendible**,
no como una API pública abierta.

---

## Modelo de Autenticación

ERPWeb utiliza **autenticación basada en sesión de Django**.

- El login se realiza en `/accounts/login/`
- La sesión es reutilizada por:
  - usuarios humanos (navegador)
  - PowerShell / Postman
  - scripts internos
  - integraciones técnicas

No se utilizan tokens ni JWT en el MVP.

La seguridad se apoya en:
- sesión autenticada
- cookies de sesión
- RBAC explícito
- reglas de negocio en backend

---

## Modelo de Autorización (RBAC)

Toda acción de negocio está protegida por un sistema propio de
**Control de Acceso Basado en Roles (RBAC)**.

- Cada vista declara explícitamente el permiso requerido:
  ```python
  @require_permission("modulo.accion")
