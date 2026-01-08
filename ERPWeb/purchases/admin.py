from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet

from .models import Supplier, PurchaseOrder, PurchaseOrderLine


@admin.action(description="Confirmar compras seleccionadas")
def confirm_purchases(modeladmin, request, queryset):
    ok = 0
    errors = 0

    for po in queryset:
        try:
            po.confirm(request.user)
            ok += 1
        except ValidationError as e:
            errors += 1
            modeladmin.message_user(
                request,
                f"PO#{po.id}: {e}",
                level=messages.ERROR,
            )
        except Exception as e:
            errors += 1
            modeladmin.message_user(
                request,
                f"PO#{po.id}: Error inesperado: {e}",
                level=messages.ERROR,
            )

    if ok:
        modeladmin.message_user(
            request,
            f"{ok} compra(s) confirmada(s) correctamente.",
            level=messages.SUCCESS,
        )
    if errors and not ok:
        modeladmin.message_user(
            request,
            "No se pudo confirmar ninguna compra. Revisá los errores.",
            level=messages.ERROR,
        )


@admin.action(description="Recibir compras seleccionadas")
def receive_purchases(modeladmin, request, queryset):
    ok = 0
    errors = 0

    for po in queryset:
        try:
            po.receive(request.user)
            ok += 1
        except ValidationError as e:
            errors += 1
            modeladmin.message_user(
                request,
                f"PO#{po.id}: {e}",
                level=messages.ERROR,
            )
        except Exception as e:
            errors += 1
            modeladmin.message_user(
                request,
                f"PO#{po.id}: Error inesperado: {e}",
                level=messages.ERROR,
            )

    if ok:
        modeladmin.message_user(
            request,
            f"{ok} compra(s) recibida(s) correctamente.",
            level=messages.SUCCESS,
        )
    if errors and not ok:
        modeladmin.message_user(
            request,
            "No se pudo recibir ninguna compra. Revisá los errores.",
            level=messages.ERROR,
        )


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "tax_id", "email", "phone", "is_active", "updated_at")
    search_fields = ("name", "tax_id", "email")
    list_filter = ("is_active",)
    readonly_fields = ("created_at", "updated_at")
    ordering = ("name",)


class PurchaseOrderLineInlineFormSet(BaseInlineFormSet):
    """
    Evita editar/agregar/borrar líneas cuando la PO NO está en DRAFT.
    Mantiene coherencia con clean() del modelo.
    """

    def clean(self):
        super().clean()

        po = getattr(self, "instance", None)
        if not po or not getattr(po, "pk", None):
            return

        if po.status != PurchaseOrder.STATUS_DRAFT:
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


class PurchaseOrderLineInline(admin.TabularInline):
    model = PurchaseOrderLine
    formset = PurchaseOrderLineInlineFormSet
    extra = 0

    fields = ("product", "quantity", "unit_cost")
    autocomplete_fields = ("product",)
    show_change_link = True


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "supplier", "status", "created_by", "created_at", "confirmed_at", "received_at")
    list_filter = ("status", "created_at")
    search_fields = ("supplier__name", "supplier__tax_id", "supplier_invoice", "note")
    ordering = ("-created_at",)

    readonly_fields = ("created_at", "updated_at", "confirmed_at", "confirmed_by", "received_at", "received_by")
    inlines = [PurchaseOrderLineInline]

    fieldsets = (
        ("Proveedor", {"fields": ("supplier", "supplier_invoice")}),
        ("Estado", {"fields": ("status",)}),
        ("Notas", {"fields": ("note",)}),
        (
            "Auditoría",
            {
                "fields": (
                    "created_by",
                    "created_at",
                    "confirmed_by",
                    "confirmed_at",
                    "received_by",
                    "received_at",
                    "updated_at",
                )
            },
        ),
    )

    actions = [confirm_purchases, receive_purchases]

    def save_model(self, request, obj, form, change):
        """
        Setea created_by automáticamente al crear desde Admin.
        """
        if not change and not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(PurchaseOrderLine)
class PurchaseOrderLineAdmin(admin.ModelAdmin):
    """
    Registro opcional para buscar líneas directamente si lo necesitás.
    """
    list_display = ("id", "purchase_order", "product", "quantity", "unit_cost")
    list_filter = ("purchase_order__status",)
    search_fields = ("purchase_order__id", "product__sku", "product__name")
    autocomplete_fields = ("purchase_order", "product")
