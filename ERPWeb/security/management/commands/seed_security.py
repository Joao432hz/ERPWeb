from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

from security.models import Role, Permission, RolePermission, UserRole


class Command(BaseCommand):
    help = "Crea/actualiza roles y permisos iniciales para el módulo Security"

    def handle(self, *args, **options):
        # 1) Definir permisos base (códigos)
        perms = [
            ("security.role.view", "Ver roles"),
            ("security.role.create", "Crear roles"),
            ("security.role.edit", "Editar roles"),
            ("security.role.delete", "Eliminar roles"),
            ("security.permission.view", "Ver permisos"),
            ("security.userrole.assign", "Asignar roles a usuarios"),
        ]

        # 2) Crear/actualizar permisos
        perm_objs = {}
        for code, desc in perms:
            obj, created = Permission.objects.get_or_create(
                code=code,
                defaults={"description": desc},
            )
            if not created and obj.description != desc:
                obj.description = desc
                obj.save()

            perm_objs[code] = obj

        self.stdout.write(self.style.SUCCESS(f"Permisos OK: {len(perm_objs)}"))

        # 3) Crear/actualizar roles
        admin_role, _ = Role.objects.get_or_create(
            name="Admin",
            defaults={"description": "Acceso total al sistema", "is_active": True},
        )

        self.stdout.write(self.style.SUCCESS("Rol Admin OK"))

        # 4) Vincular permisos al rol Admin (idempotente)
        for code, _ in perms:
            RolePermission.objects.get_or_create(
                role=admin_role,
                permission=perm_objs[code],
            )

        self.stdout.write(self.style.SUCCESS("RolePermission Admin OK"))

        # 5) (Opcional) Asignar rol Admin al superuser "admin" si existe
        User = get_user_model()
        try:
            admin_user = User.objects.get(username="admin")
            UserRole.objects.get_or_create(user=admin_user, role=admin_role)
            self.stdout.write(self.style.SUCCESS("UserRole: admin -> Admin OK"))
        except User.DoesNotExist:
            self.stdout.write(self.style.WARNING("No existe usuario 'admin' (saltado)"))

        self.stdout.write(self.style.SUCCESS("Seed finalizado ✅"))
