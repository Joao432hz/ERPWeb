from django.contrib import admin
from django.core.exceptions import ValidationError

from .models import Product, StockMovement

# ✅ Import opcional (no rompe si en algún momento se renombra)
try:
    from .models import ProductLookupCache
except Exception:
    ProductLookupCache = None


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

    # 🔒 Defensa en profundidad
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


# ✅ Cache persistente de búsquedas (cuando exista)
if ProductLookupCache is not None:

    @admin.register(ProductLookupCache)
    class ProductLookupCacheAdmin(admin.ModelAdmin):
        # Lo que querías, pero con los campos reales
        list_display = ("query_norm", "found", "hits", "updated_at")
        list_filter = ("found", "kind", "created_at", "updated_at")
        search_fields = ("query_norm", "query_raw")
        ordering = ("-updated_at",)

        # Cache: solo lectura (para evitar “toquetear” manualmente)
        readonly_fields = (
            "kind",
            "query_norm",
            "query_raw",
            "found",
            "expires_at",
            "payload",
            "hits",
            "last_hit_at",
            "created_at",
            "updated_at",
        )

        def has_module_permission(self, request):
            return request.user.is_superuser

        def has_view_permission(self, request, obj=None):
            return request.user.is_superuser

        def has_add_permission(self, request):
            return False

        def has_change_permission(self, request, obj=None):
            return False

        def has_delete_permission(self, request, obj=None):
            # Esto sí lo dejamos habilitado por si querés “limpiar cache”
            return request.user.is_superuser
