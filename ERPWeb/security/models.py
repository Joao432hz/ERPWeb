from django.db import models
from django.contrib.auth.models import User


class Role(models.Model):
    name = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def permission_codes(self):
        """
        Devuelve un set() con los códigos de permisos asociados a este rol.
        Respeta is_active del rol (si el rol está inactivo, devuelve vacío).
        No cambia DB. Útil para UI / RBAC.
        """
        if not self.is_active:
            return set()
        return set(
            self.permissions.select_related("permission")
            .values_list("permission__code", flat=True)
        )


class Permission(models.Model):
    code = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return self.code


class RolePermission(models.Model):
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="permissions")
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE)

    class Meta:
        unique_together = ("role", "permission")
        ordering = ["role__name", "permission__code"]

    def __str__(self) -> str:
        return f"{self.role.name} → {self.permission.code}"


class UserRole(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="roles")
    role = models.ForeignKey(Role, on_delete=models.CASCADE)
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "role")
        ordering = ["user__username", "role__name"]

    def __str__(self) -> str:
        return f"{self.user.username} → {self.role.name}"

    @staticmethod
    def permission_codes_for_user(user: User):
        """
        Devuelve un set() de permission.code para el usuario, considerando:
        - roles asignados al user (UserRole)
        - solo roles is_active=True
        - permisos asociados por RolePermission

        Esto NO modifica DB y es ideal para:
        - context processor (perm_keys)
        - sidebar dinámico
        - dashboard dinámico
        """
        if not user or not getattr(user, "is_authenticated", False):
            return set()

        qs = (
            RolePermission.objects.select_related("permission", "role")
            .filter(role__is_active=True, role__userrole__user=user)
            .values_list("permission__code", flat=True)
            .distinct()
        )
        return set(qs)
