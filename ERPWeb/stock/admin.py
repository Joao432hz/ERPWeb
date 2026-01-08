from django.contrib import admin
from django.core.exceptions import ValidationError

from .models import Product, StockMovement


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("id", "sku", "name", "stock", "updated_at")
    search_fields = ("sku", "name")

    # 🔒 Clave: stock y updated_at nunca se editan directo
    readonly_fields = ("stock", "updated_at")
    fields = ("sku", "name", "stock", "updated_at")

    def has_module_permission(self, request):
        # Solo superusers ven el módulo Stock en Admin
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        # Permitimos editar sku/name si es superuser (stock queda read-only igual)
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    # 🔒 Sello extra (defensa en profundidad):
    # si alguien intenta cambiar stock por algún camino, lo bloqueamos igual.
    def save_model(self, request, obj, form, change):
        if change and "stock" in getattr(form, "changed_data", []):
            raise ValidationError("El stock no se edita directo. Usá Movimientos de Stock (IN/OUT).")
        super().save_model(request, obj, form, change)


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("id", "product", "movement_type", "quantity", "created_by", "created_at")
    list_filter = ("movement_type", "created_at")
    search_fields = ("product__sku", "product__name", "note", "created_by__username")
    readonly_fields = ("product", "movement_type", "quantity", "note", "created_by", "created_at")

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        # Importante: NO cargar movimientos por admin.
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
