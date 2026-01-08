from __future__ import annotations

from dataclasses import dataclass

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.contrib.auth.base_user import AbstractBaseUser
from django.db import transaction

from security.models import Role, Permission, RolePermission, UserRole


User = get_user_model()


# ----------------------------
# Permisos reales detectados
# ----------------------------
ALL_PERMISSIONS = sorted({
    # sales
    "sales.order.view",
    "sales.order.create",
    "sales.order.edit",
    "sales.order.confirm",
    "sales.order.cancel",
    # purchases
    "purchases.supplier.view",
    "purchases.order.view",
    "purchases.order.create",
    "purchases.order.edit",
    "purchases.order.confirm",
    "purchases.order.receive",
    "purchases.order.cancel",
    # stock
    "stock.product.view",
    "stock.movement.view",
    "stock.movement.create",
    # finance
    "finance.movement.view",
    "finance.movement.pay",
})


# ----------------------------
# Matriz vendible (C3)
# ----------------------------
ROLE_MATRIX = {
    "Admin": ["*"],
    "Supervisor": [
        # sales
        "sales.order.view",
        "sales.order.create",
        "sales.order.edit",
        "sales.order.confirm",
        "sales.order.cancel",
        # purchases
        "purchases.supplier.view",
        "purchases.order.view",
        "purchases.order.create",
        "purchases.order.edit",
        "purchases.order.confirm",
        "purchases.order.receive",
        "purchases.order.cancel",
        # stock
        "stock.product.view",
        "stock.movement.view",
        "stock.movement.create",
        # finance
        "finance.movement.view",
        "finance.movement.pay",
    ],
    "Ventas": [
        "sales.order.view",
        "sales.order.create",
        "sales.order.edit",
        "sales.order.confirm",
        "sales.order.cancel",
        "stock.product.view",
    ],
    "Compras": [
        "purchases.supplier.view",
        "purchases.order.view",
        "purchases.order.create",
        "purchases.order.edit",
        "purchases.order.confirm",
        "purchases.order.cancel",
        "stock.product.view",
    ],
    "Deposito": [
        "stock.product.view",
        "stock.movement.view",
        "purchases.order.view",
        "purchases.order.receive",
        # Nota: stock.movement.create NO por defecto
    ],
    "Finanzas": [
        "finance.movement.view",
        "finance.movement.pay",
    ],
}


# ----------------------------
# Users demo (vendible)
# password = prefijo + "123"
# ej: supervisor_user -> supervisor123
# ----------------------------
DEMO_USERS = [
    ("ventas_user", "Ventas"),
    ("compras_user", "Compras"),
    ("deposito_user", "Deposito"),
    ("finanzas_user", "Finanzas"),
    ("supervisor_user", "Supervisor"),
]


def _prefix_password(username: str) -> str:
    prefix = (username.split("_", 1)[0] or username).strip()
    return f"{prefix}123"


# ----------------------------
# Helpers idempotentes
# ----------------------------
def ensure_permission(code: str) -> Permission:
    obj, _ = Permission.objects.get_or_create(
        code=code,
        defaults={"description": code},
    )
    return obj


def ensure_role(name: str, description: str = "") -> Role:
    obj, _ = Role.objects.get_or_create(
        name=name,
        defaults={"description": description, "is_active": True},
    )
    if not obj.is_active:
        obj.is_active = True
        obj.save(update_fields=["is_active"])
    return obj


@dataclass
class SyncResult:
    created: int = 0
    removed: int = 0


def set_role_permissions(role: Role, codes: list[str], *, sync: bool = False) -> SyncResult:
    """
    Asigna permisos a un rol.
    - Idempotente.
    - Si sync=True, remueve los RolePermission que no estén en la lista objetivo.
    """
    if "*" in codes:
        codes = ALL_PERMISSIONS

    perms_by_code = {c: ensure_permission(c) for c in codes}

    existing_qs = RolePermission.objects.filter(role=role).select_related("permission")
    existing_codes = set(existing_qs.values_list("permission__code", flat=True))

    to_create: list[RolePermission] = []
    for code, perm in perms_by_code.items():
        if code not in existing_codes:
            to_create.append(RolePermission(role=role, permission=perm))

    if to_create:
        RolePermission.objects.bulk_create(to_create)

    created_n = len(to_create)
    removed_n = 0

    if sync:
        target = set(perms_by_code.keys())
        to_remove = existing_qs.exclude(permission__code__in=target)
        removed_n = to_remove.count()
        if removed_n:
            to_remove.delete()

    return SyncResult(created=created_n, removed=removed_n)


def ensure_user(username: str) -> AbstractBaseUser:
    user, _ = User.objects.get_or_create(username=username)
    if hasattr(user, "is_active") and not user.is_active:
        user.is_active = True
        user.save(update_fields=["is_active"])
    return user


def set_user_password(user: AbstractBaseUser, password: str):
    user.set_password(password)
    user.save(update_fields=["password"])


def assign_user_role(username: str, role: Role, *, force_password: bool = False):
    user = ensure_user(username)

    if force_password:
        set_user_password(user, _prefix_password(username))

    UserRole.objects.get_or_create(user=user, role=role)
    return user


def cleanup_deposito_role(self_stdout_write):
    """
    Cleanup vendible:
    - Si existen ambos roles: "Depósito" (tilde) y "Deposito" (sin tilde),
      mueve UserRole del viejo al nuevo, borra los viejos y desactiva "Depósito".
    - Idempotente (si ya está limpio, no hace nada).
    """
    role_old = Role.objects.filter(name="Depósito").first()
    role_new = Role.objects.filter(name="Deposito").first()

    if not (role_old and role_new):
        return

    moved = 0
    skipped = 0

    for ur in UserRole.objects.filter(role=role_old).select_related("user"):
        _, created = UserRole.objects.get_or_create(user=ur.user, role=role_new)
        if created:
            moved += 1
        else:
            skipped += 1

    deleted_count, _ = UserRole.objects.filter(role=role_old).delete()

    if role_old.is_active:
        role_old.is_active = False
        role_old.save(update_fields=["is_active"])

    self_stdout_write(
        f"Cleanup roles: Depósito -> Deposito | moved={moved} skipped={skipped} deleted_old={deleted_count} "
        f"| Depósito desactivado={not role_old.is_active}"
    )


class Command(BaseCommand):
    help = "Seed RBAC C3: roles + permissions matrix (idempotente) + demo users."

    def add_arguments(self, parser):
        parser.add_argument(
            "--assign-users",
            action="store_true",
            help="Crea/asigna usuarios demo a roles (idempotente).",
        )
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Sincroniza permisos por rol: remueve RolePermission sobrantes (vendible).",
        )
        parser.add_argument(
            "--force-demo-passwords",
            action="store_true",
            help="Fuerza password de usuarios demo a <prefijo>123 aunque ya existan. Recomendado.",
        )

    @transaction.atomic
    def handle(self, *args, **opts):
        sync = bool(opts["sync"])
        assign_users = bool(opts["assign_users"])
        force_pw = bool(opts["force_demo_passwords"])

        # 0) Cleanup automático: Depósito -> Deposito
        cleanup_deposito_role(lambda msg: self.stdout.write(self.style.WARNING(msg)))

        # 1) Asegurar todos los permisos del sistema
        created_perms = 0
        for code in ALL_PERMISSIONS:
            _, created = Permission.objects.get_or_create(code=code, defaults={"description": code})
            if created:
                created_perms += 1

        # 2) Crear roles + asignar permisos por matriz
        created_links_total = 0
        removed_links_total = 0

        roles_by_name: dict[str, Role] = {}

        for role_name, perm_codes in ROLE_MATRIX.items():
            role = ensure_role(role_name)
            roles_by_name[role_name] = role

            res = set_role_permissions(role, perm_codes, sync=sync)
            created_links_total += res.created
            removed_links_total += res.removed

        self.stdout.write(self.style.SUCCESS(
            f"RBAC C3 OK. Permisos garantizados: {len(ALL_PERMISSIONS)} (nuevos creados: {created_perms}). "
            f"Nuevas asignaciones role-permission: {created_links_total}. "
            f"Asignaciones removidas por sync: {removed_links_total}."
        ))

        # 3) Usuarios demo opcionales
        if assign_users:
            for username, role_name in DEMO_USERS:
                role = roles_by_name.get(role_name) or Role.objects.get(name=role_name)
                assign_user_role(username, role, force_password=force_pw)

            self.stdout.write(self.style.SUCCESS(
                "Usuarios demo asignados: ventas_user, compras_user, deposito_user, finanzas_user, supervisor_user."
            ))

            if force_pw:
                self.stdout.write(self.style.WARNING(
                    "Passwords demo forzados a <prefijo>123 (ej: supervisor_user -> supervisor123)."
                ))
            else:
                self.stdout.write(self.style.WARNING(
                    "Nota: NO se forzaron passwords. Si un demo user ya existía, su password puede ser antiguo."
                ))
