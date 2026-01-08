from django.apps import AppConfig
from django.db.models.signals import post_migrate


def seed_purchases_permissions(sender, **kwargs):
    """
    Se ejecuta luego de migrate. Crea permisos del módulo purchases
    en la tabla security_permission (modelo Permission propio).
    """
    # Import adentro para evitar problemas de carga circular
    from security.models import Permission

    permissions = [
        ("purchases.supplier.view",   "Ver proveedores"),
        ("purchases.supplier.create", "Crear proveedores"),
        ("purchases.supplier.update", "Editar proveedores"),
        ("purchases.supplier.delete", "Eliminar proveedores"),
    ]

    for code, desc in permissions:
        Permission.objects.get_or_create(
            code=code,
            defaults={"description": desc},
        )


class PurchasesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "purchases"

    def ready(self):
        # Conectar el seeding SOLO cuando termina migrate
        post_migrate.connect(seed_purchases_permissions, sender=self)
