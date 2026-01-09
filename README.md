# ERPWeb — Mini ERP con Django y PostgreSQL

![Django Tests](https://github.com/Joao432hz/ERPWeb/actions/workflows/tests.yml/badge.svg)

ERPWeb es un **mini ERP funcional** desarrollado con **Django** y **PostgreSQL**, diseñado con foco en reglas reales de negocio, trazabilidad, control de permisos y flujos completos de operación.

El proyecto está pensado como **pieza de portfolio profesional**, mostrando arquitectura limpia, decisiones técnicas justificadas y validaciones end-to-end.

---

## 🧩 Módulos incluidos

- **Security / RBAC**
  - Roles y permisos desacoplados de usuarios
  - Decorador `require_permission` para control de acceso
- **Stock**
  - Control de inventario con movimientos IN / OUT
  - Trazabilidad por evento (compras y ventas)
- **Purchases**
  - Órdenes de compra con flujo DRAFT → CONFIRMED → RECEIVED
  - Impacto automático en stock y finanzas
- **Sales**
  - Órdenes de venta con confirmación
  - Descuento automático de stock y generación de cobrables
- **Finance**
  - Movimientos financieros PAYABLE / RECEIVABLE
  - Estados: OPEN / PAID / VOID
  - Resumen financiero y export CSV

---

## ✨ Características clave

### 🔐 Seguridad y permisos (RBAC)
- Roles definidos por dominio (Compras, Ventas, Depósito, etc.)
- Permisos explícitos por acción
- Sin permisos directos en usuarios (modelo escalable)

### 📦 Gestión de stock
- Entrada automática al recibir compras
- Salida automática al confirmar ventas
- Registro histórico de movimientos con referencia de origen

### 🧾 Compras y Ventas
- Validaciones fuertes de estado
- Edición bloqueada fuera de DRAFT
- Auditoría de usuario y timestamps

### 💰 Finanzas (MVP vendible)
- Reglas de negocio estrictas:
  - No se puede pagar un movimiento con monto 0
  - Un movimiento PAID no puede volver a OPEN
  - VOID es estado terminal
- Generación idempotente de movimientos financieros
- Endpoints con filtros, orden y paginado
- Exportación CSV y resumen BI-friendly

---

## 🧪 Tests y CI

- Tests automatizados sobre reglas críticas de negocio
- GitHub Actions con PostgreSQL real
- CI en verde como condición para avanzar

📄 Ver detalles en: **TESTING.md**

---

## 🚀 Instalación local (Quickstart)

### 1️⃣ Crear entorno virtual
```bash
python -m venv .venv
