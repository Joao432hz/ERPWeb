# TESTING — ERPWeb

Este documento describe cómo ejecutar y validar los tests del proyecto ERPWeb,
tanto en entorno local como en CI (GitHub Actions).

El objetivo es garantizar **regresión cero** sobre reglas críticas de negocio.

---

## 🧪 Alcance de los tests

Los tests cubren principalmente:

- Reglas de negocio en **Finance**
- Estados válidos de movimientos financieros
- Idempotencia en generación de PAYABLE / RECEIVABLE
- Restricciones de pago (amount > 0)
- Estados terminales (PAID / VOID)
- Consistencia post-cierre

---

## ⚙️ Requisitos

- Python 3.11+
- PostgreSQL 14+
- Virtualenv activo
- Variables de entorno configuradas
- `DJANGO_SETTINGS_MODULE=config.settings`

---

## 🚀 Setup rápido (local)

```bash
python -m venv .venv
