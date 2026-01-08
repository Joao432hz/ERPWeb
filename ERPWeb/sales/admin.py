from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet

from .models import SalesOrder, SalesOrderLine


@admin.action(description="Confirmar ventas seleccionadas")
def confirm_sales(modeladmin, request, queryset):
    ok = 0
    errors = 0

    for so in queryset:
        try:
            so.confirm(request.user)
            ok += 1
        except ValidationError as e:
            errors += 1
            modeladmin.message_user(
                request,
                f"SO#{so.id}: {e}",
                level=messages.ERROR,
            )
        except Exception as e:
            errors += 1
            modeladmin.message_user(
                request,
                f"SO#{so.id}: Error inesperado: {e}",
                level=messages.ERROR,
            )

    if ok:
        modeladmin.message_user(
            request,
            f"{ok} venta(s) confirmada(s) correctamente.",
            level=messages.SUCCESS,
        )
    if errors and not ok:
        modeladmin.message_user(
            request,
            f"No se pudo confirmar ninguna venta. Revisá los errores.",
            level=messages.ERROR,
        )


class SalesOrderLineInlineFormSet(BaseInlineFormSet):
    """
    Evita editar/agregar/borrar líneas cuando la orden NO está en DRAFT.
    Mantiene coherencia con clean() del modelo.
    """

    def clean(self):
        super().clean()

        # Si no hay instancia todavía (order nueva sin guardar), no validamos estado
        so = getattr(self, "instance", None)
        if not so or not getattr(so, "pk", None):
            return

        if so.status != SalesOrder.STATUS_DRAFT:
            # Si intentan modificar líneas, lo frenamos.
            for form in self.forms:
                if not hasattr(form, "cleaned_data"):
                    continue
                cd = form.cleaned_data
                if not cd:
                    continue

                marked_delete = cd.get("DELETE", False)
                has_changes = form.has_changed()

                if marked_delete or has_changes:
                    raise ValidationError("No se pueden modificar líneas si la orden no está en DRAFT.")


class SalesOrderLineInline(admin.TabularInline):
    model = SalesOrderLine
    formset = SalesOrderLineInlineFormSet
    extra = 0

    fields = ("product", "quantity", "unit_price")
    autocomplete_fields = ("product",)
    show_change_link = True


@admin.register(SalesOrder)
class SalesOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "customer_name", "status", "created_by", "created_at", "confirmed_at")
    list_filter = ("status", "created_at")
    search_fields = ("customer_name", "customer_doc", "note")
    ordering = ("-created_at",)

    readonly_fields = ("created_at", "confirmed_at", "confirmed_by")
    inlines = [SalesOrderLineInline]

    fieldsets = (
        ("Cliente", {"fields": ("customer_name", "customer_doc")}),
        ("Estado", {"fields": ("status",)}),
        ("Notas", {"fields": ("note",)}),
        ("Auditoría", {"fields": ("created_by", "created_at", "confirmed_by", "confirmed_at")}),
    )

    actions = [confirm_sales]

    def save_model(self, request, obj, form, change):
        """
        Setea created_by automáticamente en creación desde Admin.
        """
        if not change and not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(SalesOrderLine)
class SalesOrderLineAdmin(admin.ModelAdmin):
    """
    Registro opcional para poder buscar líneas directamente si lo necesitás.
    """
    list_display = ("id", "sales_order", "product", "quantity", "unit_price")
    list_filter = ("sales_order__status",)
    search_fields = ("sales_order__id", "product__sku", "product__name")
    autocomplete_fields = ("sales_order", "product")
