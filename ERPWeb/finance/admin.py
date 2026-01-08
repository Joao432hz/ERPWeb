from decimal import Decimal

from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import FinancialMovement


@admin.register(FinancialMovement)
class FinancialMovementAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "movement_type",
        "source_type",
        "source_id",
        "amount",
        "status",
        "created_at",
        "paid_at",
    )
    list_filter = ("movement_type", "source_type", "status")
    search_fields = ("id", "source_id", "notes")
    ordering = ("-created_at",)

    actions = ["mark_selected_as_paid"]

    # ----------------------------
    # Admin Actions
    # ----------------------------
    @admin.action(description="Marcar movimientos seleccionados como pagados (PAID)")
    def mark_selected_as_paid(self, request, queryset):
        """
        Marca como PAID solo movimientos OPEN con amount > 0.
        Usa full_clean() para respetar reglas del modelo.
        El modelo se encarga de setear paid_at automáticamente.

        Harden:
        - Requiere permiso finance.movement.pay (tanto para ver como para ejecutar)
        - atomic + select_for_update para evitar doble pago concurrente en Admin
        - auditoría opcional: paid_by si existe en el modelo
        """
        # Seguridad: aunque no se muestre la action, evitamos ejecución directa
        if not request.user.has_perm("finance.movement.pay"):
            self.message_user(
                request,
                "No tenés permiso para pagar movimientos (finance.movement.pay).",
                level=messages.ERROR,
            )
            return

        updated = 0
        skipped_already_paid = 0
        skipped_not_open = 0
        skipped_invalid_amount = 0
        skipped_validation = 0
        skipped_not_found = 0

        # Importante: lock por filas para evitar doble pago si dos admins ejecutan a la vez.
        ids = list(queryset.values_list("id", flat=True))

        try:
            with transaction.atomic():
                locked_qs = FinancialMovement.objects.select_for_update().filter(id__in=ids)

                for fm in locked_qs:
                    if fm.status == FinancialMovement.Status.PAID:
                        skipped_already_paid += 1
                        continue

                    if fm.status != FinancialMovement.Status.OPEN:
                        skipped_not_open += 1
                        continue

                    if (fm.amount or Decimal("0.00")) <= Decimal("0.00"):
                        skipped_invalid_amount += 1
                        continue

                    # Auditoría opcional si existe en el modelo
                    if hasattr(fm, "paid_by_id"):
                        fm.paid_by = request.user

                    fm.status = FinancialMovement.Status.PAID

                    try:
                        fm.full_clean()
                        update_fields = ["status", "paid_at"]
                        if hasattr(fm, "paid_by_id"):
                            update_fields.append("paid_by")
                        fm.save(update_fields=update_fields)
                        updated += 1
                    except ValidationError:
                        skipped_validation += 1

            # Si el queryset original tenía IDs que ya no existen, reportamos aparte
            if len(ids) != locked_qs.count():
                skipped_not_found = len(ids) - locked_qs.count()

        except Exception:
            self.message_user(
                request,
                "Error inesperado al ejecutar la acción de pago. No se aplicaron cambios parciales fuera de la transacción.",
                level=messages.ERROR,
            )
            return

        if updated:
            self.message_user(
                request,
                f"{updated} movimiento(s) marcado(s) como PAID correctamente.",
                level=messages.SUCCESS,
            )

        # Mensajes informativos (no “error”)
        if skipped_already_paid:
            self.message_user(
                request,
                f"{skipped_already_paid} omitido(s): ya estaban en PAID.",
                level=messages.INFO,
            )

        if skipped_not_open:
            self.message_user(
                request,
                f"{skipped_not_open} omitido(s): no estaban en estado OPEN.",
                level=messages.INFO,
            )

        if skipped_invalid_amount:
            self.message_user(
                request,
                f"{skipped_invalid_amount} omitido(s): amount <= 0.",
                level=messages.WARNING,
            )

        if skipped_validation:
            self.message_user(
                request,
                f"{skipped_validation} omitido(s): validación del modelo falló.",
                level=messages.WARNING,
            )

        if skipped_not_found:
            self.message_user(
                request,
                f"{skipped_not_found} omitido(s): ya no existen en la base (fueron borrados).",
                level=messages.WARNING,
            )

    # ----------------------------
    # Harden: controlar visibilidad de actions por permiso
    # ----------------------------
    def get_actions(self, request):
        actions = super().get_actions(request)
        # Oculta la action si el usuario no puede pagar
        if not request.user.has_perm("finance.movement.pay"):
            actions.pop("mark_selected_as_paid", None)
        return actions

    # ----------------------------
    # Harden: bloquear edición si PAID
    # ----------------------------
    def has_change_permission(self, request, obj=None):
        """
        Si el movimiento está PAID, bloqueamos la edición para evitar adulteración contable.
        (Se puede seguir viendo.)
        """
        perm = super().has_change_permission(request, obj=obj)
        if not perm:
            return False
        if obj is not None and obj.status == FinancialMovement.Status.PAID:
            return False
        return True

    def has_delete_permission(self, request, obj=None):
        """
        Bloqueamos borrado de movimientos PAID (auditoría).
        """
        perm = super().has_delete_permission(request, obj=obj)
        if not perm:
            return False
        if obj is not None and obj.status == FinancialMovement.Status.PAID:
            return False
        return True
