# security/admin.py
from django.contrib import admin, messages
from django.db.models import Count
from django.core.exceptions import ValidationError

from .models import Role, Permission, RolePermission, UserRole


# -----------------------------
# Inlines (para UX en Role)
# -----------------------------

class RolePermissionInline(admin.TabularInline):
    model = RolePermission
    extra = 0
    autocomplete_fields = ["permission"]
    verbose_name = "Permiso asignado"
    verbose_name_plural = "Permisos asignados"

    def has_delete_permission(self, request, obj=None):
        # Permitimos borrar asignaciones (no borra el permiso en sí)
        return True


class UserRoleInline(admin.TabularInline):
    model = UserRole
    extra = 0
    autocomplete_fields = ["user"]
    readonly_fields = ["assigned_at"]
    verbose_name = "Usuario con este rol"
    verbose_name_plural = "Usuarios con este rol"

    def has_delete_permission(self, request, obj=None):
        return True


# -----------------------------
# Role
# -----------------------------

@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "perm_count", "user_count")
    search_fields = ("name",)
    list_filter = ("is_active",)
    ordering = ("name",)
    inlines = [RolePermissionInline, UserRoleInline]
    actions = ["activate_roles", "deactivate_roles"]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _perm_count=Count("permissions", distinct=True),
            _user_count=Count("userrole", distinct=True),
        )

    @admin.display(description="Permisos", ordering="_perm_count")
    def perm_count(self, obj):
        return getattr(obj, "_perm_count", 0)

    @admin.display(description="Usuarios", ordering="_user_count")
    def user_count(self, obj):
        return getattr(obj, "_user_count", 0)

    @admin.action(description="Activar roles seleccionados")
    def activate_roles(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f"{updated} rol(es) activado(s).", level=messages.SUCCESS)

    @admin.action(description="Desactivar roles seleccionados")
    def deactivate_roles(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f"{updated} rol(es) desactivado(s).", level=messages.WARNING)


# -----------------------------
# Permission
# -----------------------------

@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ("code", "description", "role_count")
    search_fields = ("code", "description")
    ordering = ("code",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(_role_count=Count("rolepermission", distinct=True))

    @admin.display(description="Roles", ordering="_role_count")
    def role_count(self, obj):
        return getattr(obj, "_role_count", 0)


# -----------------------------
# RolePermission (asignación)
# -----------------------------

@admin.register(RolePermission)
class RolePermissionAdmin(admin.ModelAdmin):
    list_display = ("role", "permission")
    list_filter = ("role",)
    search_fields = ("role__name", "permission__code")
    autocomplete_fields = ["role", "permission"]
    ordering = ("role__name", "permission__code")

    def save_model(self, request, obj, form, change):
        """
        Mensaje más claro si intentan duplicar (role, permission).
        """
        try:
            super().save_model(request, obj, form, change)
        except Exception as e:
            # Unique constraint suele explotar como IntegrityError,
            # pero preferimos mensaje amigable para operación.
            raise ValidationError("Esta asignación Rol→Permiso ya existe.") from e


# -----------------------------
# UserRole (asignación)
# -----------------------------

@admin.register(UserRole)
class UserRoleAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "assigned_at")
    list_filter = ("role",)
    search_fields = ("user__username", "user__email", "role__name")
    autocomplete_fields = ["user", "role"]
    readonly_fields = ["assigned_at"]
    ordering = ("-assigned_at",)

    def save_model(self, request, obj, form, change):
        """
        Mensaje más claro si intentan duplicar (user, role).
        """
        try:
            super().save_model(request, obj, form, change)
        except Exception as e:
            raise ValidationError("Este usuario ya tiene asignado ese rol.") from e
