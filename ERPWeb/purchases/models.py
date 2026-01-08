from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from stock.models import Product, StockMovement

# ✅ Hook a finanzas (import seguro para no romper si finance aún no está creado/migrado)
try:
    from finance.services import ensure_payable_for_purchase
except Exception:
    ensure_payable_for_purchase = None


MONEY_Q = Decimal("0.01")


def _money(value) -> Decimal:
    """
    Normaliza a 2 decimales (monto dinero).
    """
    if value is None:
        value = Decimal("0.00")
    try:
        d = Decimal(str(value))
    except Exception:
        d = Decimal("0.00")
    return d.quantize(MONEY_Q, rounding=ROUND_HALF_UP)


def _dec(value, default=Decimal("0.00")) -> Decimal:
    if value is None or value == "":
        return default
    try:
        return Decimal(str(value))
    except Exception:
        raise ValidationError(f"Decimal inválido: {value}")


def _safe_call_finance_hook(fn, *, purchase_order, amount: Decimal):
    """
    Llama el hook de finanzas de forma retrocompatible:
    - Si el servicio acepta `amount`, lo enviamos.
    - Si no, lo llamamos con la firma vieja.
    """
    if not fn:
        return
    try:
        # Intento con amount (firma nueva)
        fn(purchase_order, amount=amount)
    except TypeError:
        # Firma vieja (solo PO)
        fn(purchase_order)


class Supplier(models.Model):
    name = models.CharField(max_length=180)
    tax_id = models.CharField(max_length=50, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    phone = models.CharField(max_length=50, blank=True, default="")
    address = models.CharField(max_length=255, blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        indexes = [
            models.Index(fields=["is_active", "name"], name="supplier_active_name_idx"),
        ]

    def __str__(self):
        return self.name


class PurchaseOrder(models.Model):
    STATUS_DRAFT = "DRAFT"
    STATUS_CONFIRMED = "CONFIRMED"
    STATUS_RECEIVED = "RECEIVED"
    STATUS_CANCELLED = "CANCELLED"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_CONFIRMED, "Confirmed"),
        (STATUS_RECEIVED, "Received"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="orders")
    supplier_invoice = models.CharField(max_length=80, blank=True, default="")

    status = models.CharField(
        max_length=12,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
        db_index=True,
    )

    note = models.TextField(blank=True, default="")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="purchase_orders_created",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="purchase_orders_confirmed",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)

    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="purchase_orders_received",
    )
    received_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["status", "created_at"], name="po_status_created_idx"),
            models.Index(fields=["supplier", "created_at"], name="po_supplier_created_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                name="purchase_order_status_valid",
                check=Q(status__in=["DRAFT", "CONFIRMED", "RECEIVED", "CANCELLED"]),
            ),
        ]

    def __str__(self):
        supplier_name = getattr(self.supplier, "name", "N/A")
        return f"PO#{self.id} - {supplier_name} - {self.status}"

    def clean(self):
        # Supplier activo
        if self.supplier_id and not Supplier.objects.filter(pk=self.supplier_id, is_active=True).exists():
            raise ValidationError({"supplier": "El proveedor está inactivo."})

        # Coherencia de confirmación
        if self.status in {self.STATUS_CONFIRMED, self.STATUS_RECEIVED}:
            if self.confirmed_by_id is None or self.confirmed_at is None:
                raise ValidationError(
                    "Si la orden está CONFIRMED/RECEIVED debe tener confirmed_by y confirmed_at."
                )

        # Coherencia de recepción
        if self.status == self.STATUS_RECEIVED:
            if self.received_by_id is None or self.received_at is None:
                raise ValidationError(
                    "Si la orden está RECEIVED debe tener received_by y received_at."
                )

        # Si no está RECEIVED, no debería tener datos de recepción
        if self.status != self.STATUS_RECEIVED:
            if self.received_by_id is not None or self.received_at is not None:
                raise ValidationError(
                    "received_by/received_at solo pueden existir si la orden está en RECEIVED."
                )

    def total_amount(self) -> Decimal:
        """
        Total de la orden = suma(quantity * unit_cost) de sus líneas.
        Devuelve Decimal con 2 decimales.
        """
        total = Decimal("0.00")
        for ln in self.lines.all().only("quantity", "unit_cost"):
            qty = Decimal(str(ln.quantity or 0))
            cost = ln.unit_cost or Decimal("0.00")
            total += qty * cost
        return _money(total)

    def _validate_lines(self):
        lines = list(self.lines.select_related("product").all())
        if not lines:
            raise ValidationError("La orden no tiene líneas.")

        for line in lines:
            if line.quantity is None or int(line.quantity) <= 0:
                raise ValidationError("Cantidad inválida en una línea (debe ser > 0).")

            # ✅ Amount real: requerimos costo unitario válido (>0)
            if line.unit_cost is None or line.unit_cost <= Decimal("0.00"):
                raise ValidationError(
                    f"La línea del producto '{line.product}' debe tener unit_cost > 0 para calcular el monto."
                )

            # Producto activo (regla ERP)
            if not Product.objects.filter(pk=line.product_id, is_active=True).exists():
                raise ValidationError(
                    f"El producto '{line.product}' está inactivo. No se puede operar."
                )

        return lines

    @transaction.atomic
    def confirm(self, user):
        """
        CONFIRMAR = cerrar la PO (NO impacta stock).
        - Debe estar en DRAFT
        - Debe tener líneas válidas
        """
        if self.status != self.STATUS_DRAFT:
            raise ValidationError("Solo se puede confirmar una compra en estado DRAFT.")

        self._validate_lines()

        self.status = self.STATUS_CONFIRMED
        self.confirmed_by = user
        self.confirmed_at = timezone.now()

        # Al confirmar, garantizamos que no haya datos de recepción
        self.received_by = None
        self.received_at = None

        self.full_clean()
        self.save(
            update_fields=[
                "status",
                "confirmed_by",
                "confirmed_at",
                "received_by",
                "received_at",
                "updated_at",
            ]
        )

    @transaction.atomic
    def receive(self, user):
        """
        RECIBIR = impactar stock (MVP: recepción total en 1 paso).
        - Debe estar CONFIRMED
        - Genera movimientos IN por cada línea
        - Pasa a RECEIVED
        - ✅ (MVP Finanzas) Crea movimiento PAYABLE asociado a esta PO con amount real
        """
        if self.status != self.STATUS_CONFIRMED:
            raise ValidationError("Solo se puede recibir una compra en estado CONFIRMED.")

        lines = self._validate_lines()

        # Movimientos IN por cada línea
        for line in lines:
            StockMovement.objects.create(
                product=line.product,
                movement_type=StockMovement.IN,
                quantity=int(line.quantity),
                note=f"Recepción PO#{self.id} - {self.supplier.name}".strip(),
                created_by=user,
            )

        self.status = self.STATUS_RECEIVED
        self.received_by = user
        self.received_at = timezone.now()

        # Confirmación debe existir para llegar a RECEIVED
        if self.confirmed_by_id is None:
            self.confirmed_by = user
        if self.confirmed_at is None:
            self.confirmed_at = timezone.now()

        self.full_clean()
        self.save(
            update_fields=[
                "status",
                "received_by",
                "received_at",
                "confirmed_by",
                "confirmed_at",
                "updated_at",
            ]
        )

        # ✅ Hook automático a Finanzas (retrocompatible)
        amount = self.total_amount()
        _safe_call_finance_hook(ensure_payable_for_purchase, purchase_order=self, amount=amount)

    @transaction.atomic
    def cancel(self, user=None):
        """
        Cancelación simple (MVP):
        - DRAFT -> CANCELLED
        - CONFIRMED -> CANCELLED (solo si NO fue recibida)
        """
        if self.status in {self.STATUS_RECEIVED, self.STATUS_CANCELLED}:
            raise ValidationError("No se puede cancelar una orden ya recibida o ya cancelada.")

        self.status = self.STATUS_CANCELLED

        # Cancelada => NO tiene recepción
        self.received_by = None
        self.received_at = None

        self.full_clean()
        self.save(update_fields=["status", "received_by", "received_at", "updated_at"])


class PurchaseOrderLine(models.Model):
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()

    # ✅ Amount real (MVP pro): costo unitario “snapshot”
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        ordering = ("id",)
        constraints = [
            models.CheckConstraint(
                name="purchase_order_line_quantity_positive",
                check=Q(quantity__gt=0),
            ),
            models.CheckConstraint(
                name="purchase_order_line_unit_cost_nonnegative",
                check=Q(unit_cost__gte=0),
            ),
        ]
        indexes = [
            models.Index(fields=["purchase_order", "product"], name="po_line_po_prod_idx"),
        ]

    def __str__(self):
        sku = getattr(self.product, "sku", "N/A")
        return f"PO#{self.purchase_order_id} - {sku} x {self.quantity} @ {self.unit_cost}"

    @property
    def line_total(self) -> Decimal:
        return _money(Decimal(str(self.quantity or 0)) * (self.unit_cost or Decimal("0.00")))

    def clean(self):
        if self.quantity is None or int(self.quantity) <= 0:
            raise ValidationError({"quantity": "quantity debe ser > 0."})

        if self.unit_cost is None:
            raise ValidationError({"unit_cost": "unit_cost es requerido (>= 0)."})
        if self.unit_cost < Decimal("0.00"):
            raise ValidationError({"unit_cost": "unit_cost debe ser >= 0."})

        if self.product_id and not Product.objects.filter(pk=self.product_id, is_active=True).exists():
            raise ValidationError({"product": "El producto está inactivo."})

        # Si la PO no está DRAFT, no se permite editar líneas (regla ERP)
        if self.purchase_order_id:
            po = getattr(self, "purchase_order", None)
            po_status = None
            if po is not None and getattr(po, "status", None) is not None:
                po_status = po.status
            else:
                po_status = PurchaseOrder.objects.filter(pk=self.purchase_order_id).values_list("status", flat=True).first()

            if po_status and po_status != PurchaseOrder.STATUS_DRAFT:
                raise ValidationError("No se pueden modificar líneas si la orden no está en DRAFT.")
