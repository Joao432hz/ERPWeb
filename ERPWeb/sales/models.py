from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from stock.models import StockMovement, Product

# ✅ Hook a finanzas (import seguro para no romper si finance aún no está creado/migrado)
try:
    from finance.services import ensure_receivable_for_sale
except Exception:
    ensure_receivable_for_sale = None

# ✅ Hook opcional para cancelación (si existe lo usamos; si no, no rompe)
try:
    from finance.services import void_receivable_for_sale  # (so, reason=...)
except Exception:
    void_receivable_for_sale = None


MONEY_Q = Decimal("0.01")


def _money(value) -> Decimal:
    """
    Normaliza a 2 decimales (monto dinero).
    - Acepta None / str / int / float / Decimal
    - Devuelve Decimal quantized 0.01
    """
    if value is None:
        value = Decimal("0.00")
    try:
        d = Decimal(str(value))
    except Exception:
        d = Decimal("0.00")
    return d.quantize(MONEY_Q, rounding=ROUND_HALF_UP)


def _safe_call_finance_hook(fn, *, sales_order, amount: Decimal):
    """
    Llama el hook de finanzas de forma retrocompatible:
    - Si el servicio acepta `amount`, lo enviamos.
    - Si no, lo llamamos con la firma vieja.
    """
    if not fn:
        return
    try:
        fn(sales_order, amount=amount)
    except TypeError:
        fn(sales_order)


def _safe_call_finance_void(fn, *, sales_order, reason: str):
    """
    Hook opcional para “anular”/“cancelar” el receivable.
    No rompe si el servicio no existe o su firma difiere.
    """
    if not fn:
        return
    try:
        fn(sales_order, reason=reason)
    except TypeError:
        try:
            fn(sales_order)
        except Exception:
            return


class SalesOrder(models.Model):
    STATUS_DRAFT = "DRAFT"
    STATUS_CONFIRMED = "CONFIRMED"
    STATUS_CANCELLED = "CANCELLED"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_CONFIRMED, "Confirmed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    customer_name = models.CharField(max_length=255)
    customer_doc = models.CharField(max_length=50, blank=True, default="")
    note = models.TextField(blank=True, default="")

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
        db_index=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="sales_orders_created",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sales_orders_confirmed",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)

    # ✅ Auditoría de cancelación (PASO 4)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sales_orders_cancelled",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Orden de Venta"
        verbose_name_plural = "Órdenes de Venta"
        indexes = [
            models.Index(fields=["status", "created_at"], name="so_status_created_idx"),
        ]

    def __str__(self):
        return f"SO#{self.id} - {self.customer_name} ({self.status})"

    @property
    def items(self):
        return self.sales_lines.all()

    @property
    def lines(self):
        return self.sales_lines.all()

    def total_amount(self) -> Decimal:
        """
        Total de la venta = suma(quantity * unit_price) de sus líneas.
        Devuelve Decimal con 2 decimales.
        """
        total = Decimal("0.00")
        for ln in self.sales_lines.all().only("quantity", "unit_price"):
            qty = Decimal(str(ln.quantity or 0))
            price = ln.unit_price or Decimal("0.00")
            total += qty * price
        return _money(total)

    def confirm(self, user):
        """
        Confirma la venta:
        - Solo desde DRAFT
        - Debe tener líneas
        - Lock de SO y Products
        - Valida stock
        - Genera movimientos OUT
        - Pasa a CONFIRMED
        - ✅ Finanzas: ensure_receivable_for_sale con amount real
        """
        if self.status != self.STATUS_DRAFT:
            raise ValidationError("Solo se puede confirmar una venta en DRAFT")

        if not self.sales_lines.exists():
            raise ValidationError("No se puede confirmar una venta sin ítems")

        with transaction.atomic():
            locked_so = SalesOrder.objects.select_for_update().get(pk=self.pk)

            if locked_so.status != locked_so.STATUS_DRAFT:
                raise ValidationError("Solo se puede confirmar una venta en DRAFT")

            lines_qs = (
                locked_so.sales_lines.select_related("product")
                .all()
                .only(
                    "id",
                    "product_id",
                    "quantity",
                    "unit_price",
                    "product__id",
                    "product__sku",
                    "product__name",
                    "product__is_active",
                )
            )
            if not lines_qs.exists():
                raise ValidationError("No se puede confirmar una venta sin ítems")

            product_ids = list(lines_qs.values_list("product_id", flat=True).distinct())

            products = {
                p.id: p
                for p in Product.objects.select_for_update().filter(id__in=product_ids)
            }

            for ln in lines_qs:
                p = products.get(ln.product_id)
                if p is None:
                    raise ValidationError("Producto inexistente en la venta")

                if ln.quantity is None or ln.quantity <= 0:
                    raise ValidationError("Cantidad inválida")

                # Regla de negocio: unit_price > 0 para amount real
                if ln.unit_price is None or ln.unit_price <= Decimal("0.00"):
                    raise ValidationError(
                        f"La línea del producto '{ln.product}' debe tener unit_price > 0 para calcular el monto."
                    )

                if not p.is_active:
                    raise ValidationError(f"El producto {p.sku} está inactivo. No se permiten ventas.")

                if p.stock < ln.quantity:
                    raise ValidationError(
                        f"Stock insuficiente para {p.sku} ({p.name}). "
                        f"Stock={p.stock}, requerido={ln.quantity}"
                    )

            # Movimientos OUT
            for ln in lines_qs:
                StockMovement.objects.create(
                    product=ln.product,
                    movement_type=StockMovement.OUT,
                    quantity=ln.quantity,
                    note=f"Venta SO#{locked_so.id} - {locked_so.customer_name}",
                    created_by=user,
                )

            locked_so.status = locked_so.STATUS_CONFIRMED
            locked_so.confirmed_by = user
            locked_so.confirmed_at = timezone.now()
            locked_so.save(update_fields=["status", "confirmed_by", "confirmed_at"])

            amount = locked_so.total_amount()
            _safe_call_finance_hook(ensure_receivable_for_sale, sales_order=locked_so, amount=amount)

            # Refrescar self
            self.status = locked_so.status
            self.confirmed_by = locked_so.confirmed_by
            self.confirmed_at = locked_so.confirmed_at

    def cancel(self, user=None, reason: str = ""):
        """
        Cancelación vendible (PASO 4):
        - DRAFT -> CANCELLED (sin tocar stock)
        - CONFIRMED -> CANCELLED:
            - Lock SO
            - Lock Products involucrados
            - Crear StockMovement IN por cada línea (reponer)
            - Auditoría cancel_by/at/reason
            - Hook a finanzas (si existe): void_receivable_for_sale
        """
        if self.status == self.STATUS_CANCELLED:
            raise ValidationError("La venta ya está cancelada")

        if self.status not in (self.STATUS_DRAFT, self.STATUS_CONFIRMED):
            raise ValidationError("Estado inválido para cancelar")

        # Para cancelar una CONFIRMED exigimos user (auditoría + movimientos)
        if self.status == self.STATUS_CONFIRMED and user is None:
            raise ValidationError("Para cancelar una venta CONFIRMED se requiere el usuario")

        with transaction.atomic():
            locked_so = SalesOrder.objects.select_for_update().get(pk=self.pk)

            if locked_so.status == locked_so.STATUS_CANCELLED:
                raise ValidationError("La venta ya está cancelada")

            # DRAFT -> CANCELLED (sin stock)
            if locked_so.status == locked_so.STATUS_DRAFT:
                locked_so.status = locked_so.STATUS_CANCELLED
                locked_so.cancelled_at = timezone.now()
                locked_so.cancel_reason = (reason or "")[:255]

                # ✅ Solo seteamos cancelled_by si vino user
                if user is not None:
                    locked_so.cancelled_by = user
                    locked_so.save(update_fields=["status", "cancelled_by", "cancelled_at", "cancel_reason"])
                else:
                    locked_so.save(update_fields=["status", "cancelled_at", "cancel_reason"])

                # refresh self
                self.status = locked_so.status
                self.cancelled_by = locked_so.cancelled_by
                self.cancelled_at = locked_so.cancelled_at
                self.cancel_reason = locked_so.cancel_reason
                return

            # CONFIRMED -> CANCELLED (reponer stock)
            if locked_so.status != locked_so.STATUS_CONFIRMED:
                raise ValidationError("Solo se puede cancelar una venta en DRAFT o CONFIRMED")

            lines_qs = (
                locked_so.sales_lines.select_related("product")
                .all()
                .only(
                    "id",
                    "product_id",
                    "quantity",
                    "product__id",
                    "product__sku",
                    "product__name",
                )
            )
            if not lines_qs.exists():
                raise ValidationError("No se puede cancelar una venta sin ítems")

            product_ids = list(lines_qs.values_list("product_id", flat=True).distinct())

            # Lock productos para evitar carreras con otras operaciones de stock
            products = {
                p.id: p
                for p in Product.objects.select_for_update().filter(id__in=product_ids)
            }

            for ln in lines_qs:
                if products.get(ln.product_id) is None:
                    raise ValidationError("Producto inexistente en la venta")
                if ln.quantity is None or ln.quantity <= 0:
                    raise ValidationError("Cantidad inválida en línea de venta")

            cancel_note = f"Cancelación SO#{locked_so.id} - {locked_so.customer_name}"
            if reason:
                cancel_note = f"{cancel_note} - {reason}"
            cancel_note = cancel_note[:255]

            for ln in lines_qs:
                StockMovement.objects.create(
                    product=ln.product,
                    movement_type=StockMovement.IN,
                    quantity=ln.quantity,
                    note=cancel_note,
                    created_by=user,
                )

            locked_so.status = locked_so.STATUS_CANCELLED
            locked_so.cancelled_by = user
            locked_so.cancelled_at = timezone.now()
            locked_so.cancel_reason = (reason or "")[:255]
            locked_so.save(update_fields=["status", "cancelled_by", "cancelled_at", "cancel_reason"])

            # Hook finanzas (no rompe si no existe)
            _safe_call_finance_void(
                void_receivable_for_sale,
                sales_order=locked_so,
                reason=locked_so.cancel_reason,
            )

            # refresh self
            self.status = locked_so.status
            self.cancelled_by = locked_so.cancelled_by
            self.cancelled_at = locked_so.cancelled_at
            self.cancel_reason = locked_so.cancel_reason


class SalesOrderLine(models.Model):
    sales_order = models.ForeignKey(
        SalesOrder,
        on_delete=models.CASCADE,
        related_name="sales_lines",
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()

    # ✅ Amount real (MVP pro): precio unitario “snapshot”
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        unique_together = [("sales_order", "product")]
        verbose_name = "Ítem de Orden de Venta"
        verbose_name_plural = "Ítems de Órdenes de Venta"
        constraints = [
            models.CheckConstraint(
                name="sales_order_line_quantity_positive",
                check=Q(quantity__gt=0),
            ),
            models.CheckConstraint(
                name="sales_order_line_unit_price_nonnegative",
                check=Q(unit_price__gte=0),
            ),
        ]

    def __str__(self):
        return f"SO#{self.sales_order_id} - {self.product_id} x {self.quantity} @ {self.unit_price}"

    @property
    def line_total(self) -> Decimal:
        return _money(Decimal(str(self.quantity or 0)) * (self.unit_price or Decimal("0.00")))

    def clean(self):
        if self.quantity is None or int(self.quantity) <= 0:
            raise ValidationError({"quantity": "quantity debe ser > 0."})

        if self.unit_price is None:
            raise ValidationError({"unit_price": "unit_price es requerido (>= 0)."})
        self.unit_price = _money(self.unit_price)
        if self.unit_price < Decimal("0.00"):
            raise ValidationError({"unit_price": "unit_price debe ser >= 0."})

        if self.product_id and not Product.objects.filter(pk=self.product_id, is_active=True).exists():
            raise ValidationError({"product": "El producto está inactivo."})

        if self.sales_order_id:
            so = getattr(self, "sales_order", None)
            if so is not None and getattr(so, "status", None) is not None:
                so_status = so.status
            else:
                so_status = SalesOrder.objects.filter(pk=self.sales_order_id).values_list("status", flat=True).first()

            if so_status and so_status != SalesOrder.STATUS_DRAFT:
                raise ValidationError("No se pueden modificar líneas si la orden no está en DRAFT.")

    def save(self, *args, **kwargs):
        self.unit_price = _money(self.unit_price)
        super().save(*args, **kwargs)

