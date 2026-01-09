# ERPWeb — Plataforma ERP Web Modular con Django y PostgreSQL

![Django Tests](https://github.com/Joao432hz/ERPWeb/actions/workflows/tests.yml/badge.svg)

**ERPWeb** es una **plataforma ERP Web modular**, desarrollada con **Django** y **PostgreSQL**, diseñada para operar procesos reales de negocio con **seguridad por roles (RBAC)**, **trazabilidad completa**, **flujos validados end-to-end** y una **interfaz web profesional**.

El proyecto nació como una base técnica sólida y hoy evoluciona hacia un **producto ERP vendible**, escalable y adaptable a distintos tipos de empresas.

---

## 🚀 ¿Qué es ERPWeb?

ERPWeb es un **ERP 100% web**, accesible desde cualquier navegador moderno, que permite gestionar los procesos centrales de una organización:

- Seguridad y control de accesos
- Gestión de stock
- Compras
- Ventas
- Finanzas
- Operación diaria mediante una interfaz gráfica clara y controlada por permisos

Está pensado como una **plataforma base**, no como un ERP rígido.

---

## 🎯 Público objetivo

ERPWeb está orientado a:

- PYMEs
- Empresas con control de stock
- Negocios de distribución o servicios
- Equipos que necesitan separar responsabilidades (Compras, Ventas, Depósito, Finanzas)
- Empresas que buscan un **ERP propio**, personalizable y escalable

---

## 🧩 Módulos incluidos (estado actual)

### 🔐 Security / RBAC
- Roles y permisos desacoplados de usuarios
- Decorador `require_permission` para control real de acceso
- Interfaz adaptada dinámicamente a los permisos del usuario
- Validación backend (no solo visual)

### 📦 Stock
- Control de inventario con movimientos IN / OUT
- Trazabilidad completa por evento
- Historial auditable de movimientos

### 🧾 Purchases
- Órdenes de compra con flujo:
  `DRAFT → CONFIRMED → RECEIVED`
- Impacto automático en stock
- Generación de obligaciones financieras (PAYABLE)

### 🛒 Sales
- Órdenes de venta con confirmación
- Descuento automático de stock
- Generación de cobrables (RECEIVABLE)
- Cancelaciones con reversión controlada

### 💰 Finance (MVP vendible)
- Movimientos financieros PAYABLE / RECEIVABLE
- Estados: `OPEN / PAID / VOID`
- Reglas de negocio estrictas:
  - No se puede pagar un movimiento con monto 0
  - Un movimiento PAID no puede volver a OPEN
  - VOID es estado terminal
- Generación idempotente de movimientos
- Endpoints con filtros, orden y paginado
- Exportación CSV y resumen BI-friendly

---

## 🖥️ Interfaz Web (UI)

ERPWeb incluye una **interfaz web propia**, integrada al backend:

- Dashboard principal
- Sidebar dinámico según permisos (RBAC)
- Navegación por módulos
- Vistas protegidas por rol
- Pantalla de acceso restringido (403 / forbidden)
- UX pensada para uso operativo diario

Acceso desde navegador (Chrome, Edge, Firefox).

---

## ✨ Características clave

### 🔐 Seguridad real
- Roles definidos por dominio
- Permisos explícitos por acción
- Sin permisos directos en usuarios (modelo escalable)
- Backend protegido incluso ante accesos directos por URL

### 📦 Gestión de stock integrada
- Entrada automática al recibir compras
- Salida automática al confirmar ventas
- Registro histórico con referencia de origen

### 🧠 Reglas de negocio fuertes
- Validaciones de estado
- Edición bloqueada fuera de DRAFT
- Auditoría de usuario y timestamps

### 🧪 Calidad y estabilidad
- Tests automatizados sobre reglas críticas
- GitHub Actions con PostgreSQL real
- CI en verde como condición para avanzar

📄 Ver detalles en **TESTING.md**

---

## ⚙️ Stack tecnológico

- Backend: **Django 5.x**
- Frontend: **Django Templates + Bootstrap 5**
- Base de datos: **PostgreSQL**
- Autenticación: Django Auth
- Control de accesos: RBAC propio
- CI/CD: GitHub Actions

---

## 🚀 Instalación local (Quickstart)

### 1️⃣ Crear entorno virtual
```bash
python -m venv .venv
source .venv/bin/activate  # Linux / Mac
.venv\Scripts\activate     # Windows
