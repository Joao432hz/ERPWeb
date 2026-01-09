# HARDENING — ERPWeb

Este documento describe las consideraciones de seguridad,
configuración y despliegue para un entorno productivo del proyecto ERPWeb.

El objetivo es diferenciar claramente entre:
- entorno de desarrollo / testing
- entorno de producción real

---

## 🔐 Variables de entorno

En producción:

- Nunca versionar credenciales
- Usar `.env` o secrets del proveedor (Docker, CI/CD, PaaS)

Variables críticas:
- `SECRET_KEY`
- `DB_*`
- `DEBUG`
- `ALLOWED_HOSTS`

---

## ⚙️ Settings de Django

### Desarrollo / Testing
- `DEBUG = True`
- SQLite permitido solo para pruebas locales rápidas
- CSRF relajado solo en endpoints técnicos

### Producción
- `DEBUG = False`
- `ALLOWED_HOSTS` explícitos
- Base de datos PostgreSQL obligatoria
- CSRF y permisos estrictos

---

## 🧪 Tests y CI

Regla del proyecto:

> Si CI está en verde, no se modifica código core sin justificación técnica.

- CI usa PostgreSQL real
- Variables de entorno alineadas con producción
- Tests cubren reglas críticas de negocio

---

## 🗄️ Base de datos

Recomendaciones en producción:
- Usuario de DB con permisos mínimos
- Backups automáticos
- Separación clara entre DB prod y test

---

## 🔒 Seguridad

- RBAC obligatorio para acciones críticas
- Sin permisos directos asignados a usuarios
- Auditoría de acciones clave (timestamps + usuario)

---

## 🚀 Despliegue (sugerido)

ERPWeb puede desplegarse en:
- VPS con Docker
- Railway / Render / Fly.io
- Infraestructura propia

Recomendado:
- Gunicorn + Nginx
- Variables de entorno gestionadas externamente
- Logs centralizados

---

## 📌 Regla final

ERPWeb está diseñado para:
- No asumir estados
- No permitir operaciones inconsistentes
- Priorizar integridad de negocio sobre shortcuts técnicos
