from django.core.management.base import BaseCommand
from security.models import Permission, Role, RolePermission

class Command(BaseCommand):
    help = "Crea permisos de Stock y los asigna al rol Operador"

    def handle(self, *args, **options):
        perms_to_create = [
            ("stock.product.view", "Ver productos (Stock)"),
            ("stock.movement.view", "Ver movimientos de stock"),
            ("stock.movement.create", "Crear movimientos de stock"),
        ]

        created_count = 0
        for code, description in perms_to_create:
            _, created = Permission.objects.get_or_create(
                code=code,
                defaults={"description": description},
            )
            if created:
                created_count += 1

        self.stdout.write(self.style.SUCCESS(f"Permisos creados (si faltaban): {created_count}"))

        # Asignar al rol Operador
        try:
            operador = Role.objects.get(name__iexact="Operador")
        except Role.DoesNotExist:
            self.stdout.write(self.style.ERROR("No existe el rol 'Operador'. Crealo primero en admin."))
            return

        assigned = 0
        for code, _ in perms_to_create:
            perm = Permission.objects.get(code=code)
            _, created = RolePermission.objects.get_or_create(role=operador, permission=perm)
            if created:
                assigned += 1

        self.stdout.write(self.style.SUCCESS(f"Permisos asignados al rol Operador: {assigned}"))
