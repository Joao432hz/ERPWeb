from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q, Sum
from django.utils import timezone


class Product(models.Model):
    sku = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
        help_text="Código único del producto (SKU)",
    )
    name = models.CharField(
        max_length=255,
        help_text="Nombre del producto",
    )
    description = models.TextField(
        blank=True,
        help_text="Descripción opcional",
    )

    # 👉 Stock materializado (fuente operativa)
    stock = models.IntegerField(
        default=0,
        help_text="Cantidad actual en stock (no puede ser negativa)",
    )

    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Producto activo/inactivo",
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Producto"
        verbose_name_plural = "Productos"
        constraints = [
            models.CheckConstraint(
                name="stock_product_stock_non_negative",
                check=Q(stock__gte=0),
            ),
        ]

    def __str__(self):
        return f"{self.sku} - {self.name}"

    # ===============================
    # 🔎 MÉTODOS DE AUDITORÍA
    # ===============================

    @property
    def stock_calculated(self):
        """
        Stock calculado desde movimientos (solo para auditoría/debug).
        NO se usa para operar.
        """
        ins = (
            self.movements.filter(movement_type=StockMovement.IN)
            .aggregate(s=Sum("quantity"))["s"]
            or 0
        )
        outs = (
            self.movements.filter(movement_type=StockMovement.OUT)
            .aggregate(s=Sum("quantity"))["s"]
            or 0
        )
        return ins - outs

    def rebuild_stock_from_movements(self, *, save=True):
        """
        Recalcula el stock a partir de movimientos históricos.
        Útil para mantenimiento o reparación de datos.
        """
        new_stock = self.stock_calculated
        if new_stock < 0:
            raise ValidationError(
                f"Stock inconsistente al reconstruir ({new_stock}). Revisar movimientos."
            )

        self.stock = new_stock
        if save:
            self.save(update_fields=["stock", "updated_at"])
        return new_stock


class StockMovement(models.Model):
    # Valores persistidos
    IN = "IN"
    OUT = "OUT"

    # Alias de compatibilidad
    TYPE_IN = IN
    TYPE_OUT = OUT

    MOVEMENT_TYPES = (
        (IN, "Ingreso"),
        (OUT, "Egreso"),
    )

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="movements",
        help_text="Producto afectado por el movimiento",
    )

    movement_type = models.CharField(
        max_length=3,
        choices=MOVEMENT_TYPES,
        help_text="Tipo de movimiento: IN / OUT",
    )

    quantity = models.PositiveIntegerField(
        help_text="Cantidad del movimiento (debe ser > 0)",
    )

    note = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Notas adicionales (opcional)",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="stock_movements",
        help_text="Usuario que registró el movimiento",
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Movimiento de Stock"
        verbose_name_plural = "Movimientos de Stock"
        indexes = [
            models.Index(fields=["product", "created_at"], name="stock_mv_prod_created_idx"),
            models.Index(fields=["movement_type", "created_at"], name="stock_mv_type_created_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                name="stock_movement_type_valid",
                check=Q(movement_type__in=["IN", "OUT"]),
            ),
            models.CheckConstraint(
                name="stock_movement_quantity_positive",
                check=Q(quantity__gt=0),
            ),
        ]

    def __str__(self):
        sku = getattr(self.product, "sku", "N/A")
        created = self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else "N/A"
        return f"{sku} {self.movement_type} {self.quantity} ({created})"

    # ===============================
    # VALIDACIONES
    # ===============================

    def clean(self):
        if not self.product_id:
            raise ValidationError({"product": "Debe indicar un producto."})

        if self.quantity is None or int(self.quantity) <= 0:
            raise ValidationError({"quantity": "La cantidad debe ser mayor a 0."})

        if self.movement_type not in {self.IN, self.OUT}:
            raise ValidationError({"movement_type": "Tipo de movimiento inválido."})

        if not Product.objects.filter(pk=self.product_id, is_active=True).exists():
            raise ValidationError({"product": "El producto está inactivo."})

    # ===============================
    # LÓGICA CENTRAL DE STOCK
    # ===============================

    def save(self, *args, **kwargs):
        """
        Reglas ERP:
        - El stock se impacta SOLO al crear el movimiento
        - IN suma, OUT resta
        - No se permite stock negativo
        - Operación atómica + lock del producto
        """
        is_new = self.pk is None
        self.full_clean()

        if not is_new:
            raise ValidationError(
                "No se permite editar un movimiento existente. Creá uno nuevo."
            )

        qty = int(self.quantity)

        with transaction.atomic():
            product = Product.objects.select_for_update().get(pk=self.product_id)

            if self.movement_type == self.IN:
                new_stock = product.stock + qty

            elif self.movement_type == self.OUT:
                new_stock = product.stock - qty
                if new_stock < 0:
                    raise ValidationError(
                        f"Stock insuficiente. Actual: {product.stock}. "
                        f"Intentaste egresar: {qty}."
                    )
            else:
                raise ValidationError("Tipo de movimiento inválido.")

            super().save(*args, **kwargs)

            product.stock = new_stock
            product.updated_at = timezone.now()
            product.save(update_fields=["stock", "updated_at"])
