from django.core.management.base import BaseCommand
from django.db import transaction
from django.contrib.auth.models import User

from security.models import Role, Permission, RolePermission, UserRole


# ----------------------------
# C2: Roles funcionales
# ----------------------------
C2_ROLES = {
    "Compras": "Gestiona Purchase Orders (DRAFT), edita líneas y confirma. NO recibe.",
    "Depósito": "Recibe compras (impacta stock/finanzas) y gestiona movimientos de stock. NO confirma.",
    "Ventas": "Gestiona Sales Orders (DRAFT), edita líneas y confirma. (Impacta stock/finanzas al confirmar).",
    # Opcional (si luego querés separar finanzas):
    # "Finanzas": "Visualiza movimientos financieros y puede marcar movimientos como pagados (PAY).",
}

# ----------------------------
# C2: Permisos por rol
# (alineado a require_permission(...) en tus views actuales)
# ----------------------------
C2_ROLE_PERMISSIONS = {
    "Compras": [
        "purchases.supplier.view",
        "purchases.order.view",
        "purchases.order.create",
        "purchases.order.edit",
        "purchases.order.confirm",
        "purchases.order.cancel",
        "stock.product.view",
    ],
    "Depósito": [
        "purchases.order.view",
        "purchases.order.receive",
        "stock.product.view",
        "stock.movement.view",
        "stock.movement.create",
    ],
    "Ventas": [
        "sales.order.view",
        "sales.order.create",
        "sales.order.edit",
        "sales.order.confirm",
        "sales.order.cancel",
        "stock.product.view",
    ],

    # Opcional (si después querés rol Finanzas separado)
    # "Finanzas": [
    #     "finance.movement.view",
    #     "finance.movement.pay",
    # ],
}

# ----------------------------
# Opcional: asignación de roles a usuarios existentes
# (solo si corrés con --assign-users y existen)
# ----------------------------
C2_USER_ROLES = {
    # "compras_user": ["Compras"],
    # "deposito_user": ["Depósito"],
    # "ventas_user": ["Ventas"],
    # "finanzas_user": ["Finanzas"],
}


class Command(BaseCommand):
    help = "Seed RBAC C2 (idempotente): crea roles funcionales y asigna permisos custom."

    def add_arguments(self, parser):
        parser.add_argument(
            "--assign-users",
            action="store_true",
            help="Asigna roles a usuarios definidos en C2_USER_ROLES (solo si existen).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("RBAC C2 seed (idempotente)"))

        # 1) Roles
        roles_by_name = {}
        for name, desc in C2_ROLES.items():
            role, created = Role.objects.get_or_create(
                name=name,
                defaults={"description": desc, "is_active": True},
            )
            if not created:
                # hardening: mantener activo + actualizar descripción si cambió
                changed = False
                if role.description != desc:
                    role.description = desc
                    changed = True
                if not role.is_active:
                    role.is_active = True
                    changed = True
                if changed:
                    role.save(update_fields=["description", "is_active"])

            roles_by_name[name] = role
            self.stdout.write(f"Role: {name} ({'created' if created else 'ok'})")

        # 2) Permisos
        perm_codes = sorted({code for codes in C2_ROLE_PERMISSIONS.values() for code in codes})
        perms_by_code = {}
        for code in perm_codes:
            perm, created = Permission.objects.get_or_create(code=code, defaults={"description": ""})
            perms_by_code[code] = perm
            self.stdout.write(f"Permission: {code} ({'created' if created else 'ok'})")

        # 3) Grants role -> permission (RolePermission)
        for role_name, codes in C2_ROLE_PERMISSIONS.items():
            role = roles_by_name[role_name]
            for code in codes:
                perm = perms_by_code[code]
                rp, created = RolePermission.objects.get_or_create(role=role, permission=perm)
                if created:
                    self.stdout.write(f"  + grant {role_name} -> {code}")

        # 4) Asignación opcional de roles a usuarios existentes
        if options.get("assign_users"):
            for username, role_names in C2_USER_ROLES.items():
                try:
                    user = User.objects.get(username=username)
                except User.DoesNotExist:
                    self.stdout.write(self.style.WARNING(f"User '{username}' no existe. (omitido)"))
                    continue

                for role_name in role_names:
                    role = roles_by_name.get(role_name) or Role.objects.filter(name=role_name).first()
                    if not role:
                        self.stdout.write(self.style.WARNING(f"Role '{role_name}' no existe. (omitido)"))
                        continue

                    ur, created = UserRole.objects.get_or_create(user=user, role=role)
                    self.stdout.write(
                        f"UserRole: {username} -> {role_name} ({'created' if created else 'ok'})"
                    )

        self.stdout.write(self.style.SUCCESS("RBAC C2 seed completado."))
