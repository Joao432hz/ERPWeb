from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db import models
from django.db.models import Q
from django.core.exceptions import ValidationError
from django.utils import timezone

MONEY_Q = Decimal("0.01")


class FinancialMovement(models.Model):
    class MovementType(models.TextChoices):
        PAYABLE = "PAYABLE", "Payable"
        RECEIVABLE = "RECEIVABLE", "Receivable"

    class SourceType(models.TextChoices):
        PURCHASE = "PURCHASE", "Purchase"
        SALE = "SALE", "Sale"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        PAID = "PAID", "Paid"
        VOID = "VOID", "Void"

    movement_type = models.CharField(
        max_length=12,
        choices=MovementType.choices,
    )
    source_type = models.CharField(
        max_length=10,
        choices=SourceType.choices,
    )
    source_id = models.PositiveIntegerField()

    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    status = models.CharField(
        max_length=6,
        choices=Status.choices,
        default=Status.OPEN,
    )

    notes = models.CharField(max_length=255, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["movement_type", "source_type", "source_id"],
                name="uniq_fin_movement_per_source",
            ),

            # --- DB constraints (deben existir en el modelo para que Django NO intente removerlos) ---

            # 1) amount nunca negativo
            models.CheckConstraint(
                check=Q(amount__gte=0),
                name="fin_mov_amount_gte_0",
            ),

            # 2) status = PAID => paid_at IS NOT NULL
            models.CheckConstraint(
                check=(Q(status="PAID", paid_at__isnull=False) | ~Q(status="PAID")),
                name="fin_mov_paid_requires_paid_at",
            ),

            # 3) status != PAID => paid_at IS NULL (OPEN/VOID no deben tener paid_at)
            models.CheckConstraint(
                check=(Q(status="PAID") | Q(paid_at__isnull=True)),
                name="fin_mov_non_paid_no_paid_at",
            ),
        ]
        indexes = [
            models.Index(fields=["source_type", "source_id"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return (
            f"{self.movement_type} {self.source_type}#{self.source_id} "
            f"${self.amount} [{self.status}]"
        )

    # -------------------------
    # Acciones de negocio
    # -------------------------
    def pay(self):
        """
        Marca como PAID.
        Reglas vendibles:
        - Solo OPEN puede pagarse.
        - amount debe ser > 0.
        """
        if self.status == self.Status.VOID:
            raise ValidationError("No se puede pagar un movimiento VOID.")
        if self.status == self.Status.PAID:
            raise ValidationError("El movimiento ya está PAID.")
        if self.amount <= Decimal("0.00"):
            raise ValidationError("No se puede pagar un movimiento con amount <= 0.")
        self.status = self.Status.PAID
        # paid_at se setea en save() si corresponde
        self.save(update_fields=["status", "paid_at"])

    def void(self, reason: str = ""):
        """
        Anula el movimiento.
        Reglas vendibles (conservadoras):
        - No se puede anular si está PAID.
        - VOID es terminal: no se reabre desde el modelo.
        """
        if self.status == self.Status.PAID:
            raise ValidationError("No se puede anular un movimiento ya pagado.")
        if self.status == self.Status.VOID:
            # idempotente y silencioso: ya está anulado
            return

        self.status = self.Status.VOID
        self.paid_at = None  # garantía explícita

        if reason:
            self.notes = (reason or "")[:255]

        # Guardamos SOLO lo que tocamos
        update_fields = ["status", "paid_at"]
        if reason:
            update_fields.append("notes")

        self.save(update_fields=update_fields)

    # -------------------------
    # Validaciones de negocio
    # -------------------------
    def clean(self):
        super().clean()

        # Normalizar status/amount logicamente
        if self.amount is None:
            raise ValidationError({"amount": "amount no puede ser null."})

        # PAID exige amount > 0
        if self.status == self.Status.PAID and self.amount <= Decimal("0.00"):
            raise ValidationError(
                {"amount": "No se puede marcar como PAID un movimiento con amount <= 0."}
            )

        # VOID no debe tener paid_at
        if self.status == self.Status.VOID and self.paid_at:
            raise ValidationError({"paid_at": "Un movimiento VOID no puede tener paid_at."})

        # Inmutabilidad de campos clave si ya está cerrado (PAID/VOID)
        # (Esto evita que alguien cambie el amount a posteriori)
        if self.pk:
            try:
                prev = FinancialMovement.objects.get(pk=self.pk)
            except FinancialMovement.DoesNotExist:
                prev = None

            if prev:
                if prev.status in (self.Status.PAID, self.Status.VOID):
                    # No permitimos cambiar status (terminal) desde clean
                    if self.status != prev.status:
                        raise ValidationError({"status": "No se puede cambiar el status de un movimiento cerrado."})

                    # No permitimos cambiar campos base del movimiento
                    immutable_fields = ("movement_type", "source_type", "source_id", "amount")
                    changed = [f for f in immutable_fields if getattr(self, f) != getattr(prev, f)]
                    if changed:
                        raise ValidationError(
                            {f: "No se puede modificar este campo en un movimiento cerrado (PAID/VOID)." for f in changed}
                        )

    # -------------------------
    # Persistencia hardenizada
    # -------------------------
    def save(self, *args, **kwargs):
        # Normalizar amount a 2 decimales (siempre)
        if self.amount is not None:
            try:
                self.amount = Decimal(str(self.amount)).quantize(MONEY_Q, rounding=ROUND_HALF_UP)
            except (InvalidOperation, AttributeError, TypeError):
                # Si viene algo raro, que lo agarre la validación de Django/DB
                pass

        # Setear / limpiar paid_at según estado
        if self.status == self.Status.PAID:
            if self.amount is not None and self.amount <= Decimal("0.00"):
                # Defensa extra (aunque clean ya lo cubre)
                raise ValidationError("No se puede guardar PAID con amount <= 0.")
            if not self.paid_at:
                self.paid_at = timezone.now()
        else:
            # OPEN o VOID
            self.paid_at = None

        # Ejecutar validaciones antes de persistir (vendible)
        self.full_clean()

        super().save(*args, **kwargs)
