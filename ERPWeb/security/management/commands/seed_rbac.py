from django.core.management.base import BaseCommand
from django.db import transaction

from security.models import Role, Permission, RolePermission


# ----------------------------
# Declaración RBAC (fuente de verdad)
# ----------------------------

PERMISSIONS = [
    # SALES
    ("sales.order.view", "Ver órdenes de venta"),
    ("sales.order.create", "Crear orden de venta"),
    ("sales.order.edit", "Editar orden de venta (DRAFT)"),
    ("sales.order.confirm", "Confirmar orden de venta"),
    ("sales.order.cancel", "Cancelar orden de venta"),

    # PURCHASES
    ("purchases.supplier.view", "Ver proveedores"),
    ("purchases.order.view", "Ver órdenes de compra"),
    ("purchases.order.create", "Crear orden de compra"),
    ("purchases.order.edit", "Editar orden de compra (DRAFT)"),
    ("purchases.order.confirm", "Confirmar orden de compra"),
    ("purchases.order.receive", "Recibir orden de compra (impacta stock/finanzas)"),
    ("purchases.order.cancel", "Cancelar orden de compra"),

    # STOCK
    ("stock.product.view", "Ver productos y stock"),
    ("stock.movement.view", "Ver movimientos de stock"),
    ("stock.movement.create", "Crear movimiento manual de stock (IN/OUT)"),

    # FINANCE
    ("finance.movement.view", "Ver movimientos financieros + summary/export"),
    ("finance.movement.pay", "Pagar movimiento financiero (OPEN -> PAID)"),
]


def _all_permission_codes():
    return {code for code, _ in PERMISSIONS}


ROLE_MATRIX = {
    "Operador": {
        "sales.order.view",
        "purchases.supplier.view",
        "purchases.order.view",
        "stock.product.view",
        "stock.movement.view",
        "finance.movement.view",
    },
    "Ventas": {
        "sales.order.view",
        "sales.order.create",
        "sales.order.edit",
        "sales.order.confirm",
        "sales.order.cancel",
        "purchases.supplier.view",
        "purchases.order.view",
        "stock.product.view",
        "stock.movement.view",
        "finance.movement.view",
    },
    "Compras": {
        "purchases.supplier.view",
        "purchases.order.view",
        "purchases.order.create",
        "purchases.order.edit",
        "purchases.order.confirm",
        "purchases.order.cancel",
        "sales.order.view",
        "stock.product.view",
        "stock.movement.view",
        "finance.movement.view",
    },
    "Depósito": {
        "stock.product.view",
        "stock.movement.view",
        "stock.movement.create",
        "purchases.supplier.view",
        "purchases.order.view",
        "purchases.order.receive",
        "sales.order.view",
        "finance.movement.view",
    },
    "Finanzas": {
        "finance.movement.view",
        "finance.movement.pay",
        "sales.order.view",
        "purchases.supplier.view",
        "purchases.order.view",
        "stock.product.view",
        "stock.movement.view",
    },
    "Supervisor": _all_permission_codes(),
    "Admin": _all_permission_codes(),
}


# ----------------------------
# Command
# ----------------------------

class Command(BaseCommand):
    help = "Seed RBAC (idempotent): crea/actualiza permisos, roles y matriz rol-permiso."

    @transaction.atomic
    def handle(self, *args, **options):
        created_perms = 0
        updated_perms = 0
        created_roles = 0
        added_links = 0
        removed_links = 0

        # 0) Validación defensiva: ROLE_MATRIX no debe referenciar códigos inexistentes
        all_codes = _all_permission_codes()
        unknown = {}
        for role_name, codes in ROLE_MATRIX.items():
            bad = sorted({c for c in codes if c not in all_codes})
            if bad:
                unknown[role_name] = bad
        if unknown:
            lines = ["ROLE_MATRIX contiene permisos que no existen en PERMISSIONS:"]
            for role_name, bad in unknown.items():
                lines.append(f"- {role_name}: {bad}")
            raise ValueError("\n".join(lines))

        # 1) Permissions (create/update)
        perm_by_code = {}
        for code, description in PERMISSIONS:
            perm, was_created = Permission.objects.get_or_create(
                code=code,
                defaults={"description": description},
            )
            if was_created:
                created_perms += 1
            else:
                # Si existía pero description distinto, actualizamos (el bug era update_fields=["name"])
                if (perm.description or "") != (description or ""):
                    perm.description = description
                    perm.save(update_fields=["description"])
                    updated_perms += 1
            perm_by_code[code] = perm

        # 2) Roles + RolePermission (set exact)
        for role_name, perm_codes in ROLE_MATRIX.items():
            role, was_created = Role.objects.get_or_create(
                name=role_name,
                defaults={"is_active": True},
            )
            if was_created:
                created_roles += 1

            desired_ids = {perm_by_code[c].id for c in perm_codes}
            existing_ids = set(
                RolePermission.objects.filter(role=role).values_list("permission_id", flat=True)
            )

            to_add = desired_ids - existing_ids
            to_remove = existing_ids - desired_ids

            if to_remove:
                deleted, _ = RolePermission.objects.filter(
                    role=role, permission_id__in=to_remove
                ).delete()
                # deleted incluye cascadas; pero acá solo borramos RolePermission
                removed_links += int(deleted)

            if to_add:
                RolePermission.objects.bulk_create(
                    [RolePermission(role=role, permission_id=pid) for pid in to_add],
                    ignore_conflicts=True,
                )
                added_links += len(to_add)

        self.stdout.write(
            self.style.SUCCESS(
                "RBAC seed OK "
                f"(perms: +{created_perms}, ~{updated_perms}; "
                f"roles: +{created_roles}; "
                f"links: +{added_links}, -{removed_links})."
            )
        )

