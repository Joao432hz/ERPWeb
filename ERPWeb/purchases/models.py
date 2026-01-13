from decimal import Decimal, ROUND_HALF_UP
import os

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
        fn(purchase_order, amount=amount)
    except TypeError:
        fn(purchase_order)


# ============================================================
# SUPPLIERS (Proveedor)
# ============================================================

class Supplier(models.Model):
    TYPE_HABITUAL = "HABITUAL"
    TYPE_OCASIONAL = "OCASIONAL"
    SUPPLIER_TYPE_CHOICES = [
        (TYPE_HABITUAL, "Habitual"),
        (TYPE_OCASIONAL, "Ocasional"),
    ]

    VAT_RI = "RI"
    VAT_MONO = "MONO"
    VAT_EXENTO = "EXENTO"
    VAT_NO_RESP = "NO_RESP"
    VAT_CONS_FINAL = "CONS_FINAL"
    VAT_NO_CAT = "NO_CAT"
    VAT_CONDITION_CHOICES = [
        (VAT_RI, "Responsable Inscripto"),
        (VAT_MONO, "Monotributo"),
        (VAT_EXENTO, "Exento"),
        (VAT_NO_RESP, "No Responsable/No alcanzado"),
        (VAT_CONS_FINAL, "Consumidor Final"),
        (VAT_NO_CAT, "Sujeto No Categorizado"),
    ]

    DOC_DNI = "DNI"
    DOC_LC = "LC"
    DOC_LE = "LE"
    DOC_PASSPORT = "PASAPORTE"
    DOC_FOREIGN = "DOC_EXTRANJERO"
    DOC_CUIT = "CUIT"
    DOC_TYPE_CHOICES = [
        (DOC_DNI, "DNI"),
        (DOC_LC, "LC"),
        (DOC_LE, "LE"),
        (DOC_PASSPORT, "PASAPORTE"),
        (DOC_FOREIGN, "DOC. EXTRANJERO"),
        (DOC_CUIT, "CUIT"),
    ]

    CUR_ARS = "ARS"
    CUR_USD = "USD"
    CUR_EUR = "EUR"
    CURRENCY_CHOICES = [
        (CUR_ARS, "Peso (ARS)"),
        (CUR_USD, "Dólar (USD)"),
        (CUR_EUR, "Euro (EUR)"),
    ]

    STATUS_ACTIVE = "ACTIVE"
    STATUS_INACTIVE = "INACTIVE"
    STATUS_BLOCKED = "BLOCKED"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Activo"),
        (STATUS_INACTIVE, "Inactivo"),
        (STATUS_BLOCKED, "Bloqueado"),
    ]

    # Identificación y datos generales
    name = models.CharField(max_length=180)  # Nombre legal / razón social
    trade_name = models.CharField(max_length=180, blank=True, default="")  # Nombre comercial
    supplier_type = models.CharField(max_length=12, choices=SUPPLIER_TYPE_CHOICES, default=TYPE_HABITUAL)

    vat_condition = models.CharField(max_length=16, choices=VAT_CONDITION_CHOICES, blank=True, default="")
    tax_id = models.CharField(max_length=50, blank=True, default="")  # CUIT / NIF / Tax ID
    document_type = models.CharField(max_length=20, choices=DOC_TYPE_CHOICES, blank=True, default="")

    # Domicilios y ubicación
    fiscal_address = models.CharField(max_length=255, blank=True, default="")
    province = models.CharField(max_length=120, blank=True, default="")
    postal_code = models.CharField(max_length=30, blank=True, default="")
    country = models.CharField(max_length=120, blank=True, default="")

    # Contacto
    phone = models.CharField(max_length=50, blank=True, default="")  # Teléfono principal
    phone_secondary = models.CharField(max_length=50, blank=True, default="")
    email = models.EmailField(blank=True, default="")  # Correo principal
    email_ap = models.EmailField(blank=True, default="")  # Compras / cobranzas
    contact_name = models.CharField(max_length=120, blank=True, default="")
    contact_role = models.CharField(max_length=120, blank=True, default="")
    fax_or_web = models.CharField(max_length=180, blank=True, default="")

    # Condiciones comerciales
    payment_terms = models.JSONField(blank=True, default=list)  # checkboxes múltiples
    standard_payment_terms = models.JSONField(blank=True, default=list)  # checkboxes múltiples
    price_list_update_days = models.PositiveIntegerField(null=True, blank=True)
    transaction_currency = models.CharField(max_length=8, choices=CURRENCY_CHOICES, blank=True, default="")
    account_reference = models.CharField(max_length=120, blank=True, default="")  # cuenta corriente acreedora activa
    classification = models.CharField(max_length=120, blank=True, default="")  # sector/categoría interna
    product_category = models.CharField(max_length=120, blank=True, default="")  # categoría productos proveedor

    # Datos bancarios
    bank_name = models.CharField(max_length=120, blank=True, default="")
    bank_account_ref = models.CharField(max_length=120, blank=True, default="")  # sucursal/cbu/iban
    bank_account_type = models.CharField(max_length=80, blank=True, default="")
    bank_account_holder = models.CharField(max_length=120, blank=True, default="")
    bank_account_currency = models.CharField(max_length=8, choices=CURRENCY_CHOICES, blank=True, default="")

    # Gestión tributaria y cumplimiento
    tax_condition = models.CharField(max_length=16, choices=VAT_CONDITION_CHOICES, blank=True, default="")
    retention_category = models.CharField(max_length=120, blank=True, default="")
    retention_codes = models.CharField(max_length=180, blank=True, default="")

    # Otros
    internal_notes = models.TextField(blank=True, default="")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_ACTIVE)

    # Campos adicionales personalizables (key/value)
    extra_fields = models.JSONField(blank=True, default=dict)

    # Retro-compat (NO romper PurchaseOrder.clean que usa is_active)
    is_active = models.BooleanField(default=True, db_index=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="suppliers_created",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        indexes = [
            models.Index(fields=["is_active", "name"], name="supplier_active_name_idx"),
            models.Index(fields=["status", "name"], name="supplier_status_name_idx"),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        # status BLOCKED => no activo
        if self.status == self.STATUS_BLOCKED and self.is_active:
            # Permitimos que save() sincronice, pero evitamos inconsistencias si lo setean mal
            pass

        # Normalización básica de tax_id
        if self.tax_id:
            self.tax_id = str(self.tax_id).strip()

        # Emails: vacíos ok
        if self.email:
            self.email = self.email.strip()
        if self.email_ap:
            self.email_ap = self.email_ap.strip()

        # JSON fields: asegurar tipo
        if self.payment_terms is None:
            self.payment_terms = []
        if self.standard_payment_terms is None:
            self.standard_payment_terms = []
        if self.extra_fields is None:
            self.extra_fields = {}

        if not isinstance(self.payment_terms, list):
            raise ValidationError({"payment_terms": "payment_terms debe ser una lista."})
        if not isinstance(self.standard_payment_terms, list):
            raise ValidationError({"standard_payment_terms": "standard_payment_terms debe ser una lista."})
        if not isinstance(self.extra_fields, dict):
            raise ValidationError({"extra_fields": "extra_fields debe ser un diccionario (key/value)."})

    def save(self, *args, **kwargs):
        # 🔒 Mantener compatibilidad: status manda is_active
        self.is_active = (self.status == self.STATUS_ACTIVE)
        super().save(*args, **kwargs)


def _supplier_doc_upload_to(instance, filename: str) -> str:
    # media/suppliers/<supplier_id>/<filename>
    supplier_id = getattr(instance, "supplier_id", None) or "unknown"
    base = os.path.basename(filename or "documento")
    return f"suppliers/{supplier_id}/{base}"


class SupplierDocument(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name="documents")
    file = models.FileField(upload_to=_supplier_doc_upload_to)
    original_name = models.CharField(max_length=255, blank=True, default="")
    note = models.CharField(max_length=255, blank=True, default="")

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="supplier_documents_uploaded",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-uploaded_at",)

    def __str__(self):
        return self.original_name or os.path.basename(self.file.name or "")


# ============================================================
# PURCHASE ORDERS (tu código intacto)
# ============================================================

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
        # Supplier activo (compat: usa is_active)
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

            if line.unit_cost is None or line.unit_cost <= Decimal("0.00"):
                raise ValidationError(
                    f"La línea del producto '{line.product}' debe tener unit_cost > 0 para calcular el monto."
                )

            if not Product.objects.filter(pk=line.product_id, is_active=True).exists():
                raise ValidationError(
                    f"El producto '{line.product}' está inactivo. No se puede operar."
                )

        return lines

    @transaction.atomic
    def confirm(self, user):
        if self.status != self.STATUS_DRAFT:
            raise ValidationError("Solo se puede confirmar una compra en estado DRAFT.")

        self._validate_lines()

        self.status = self.STATUS_CONFIRMED
        self.confirmed_by = user
        self.confirmed_at = timezone.now()

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
        if self.status != self.STATUS_CONFIRMED:
            raise ValidationError("Solo se puede recibir una compra en estado CONFIRMED.")

        lines = self._validate_lines()

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

        amount = self.total_amount()
        _safe_call_finance_hook(ensure_payable_for_purchase, purchase_order=self, amount=amount)

    @transaction.atomic
    def cancel(self, user=None):
        if self.status in {self.STATUS_RECEIVED, self.STATUS_CANCELLED}:
            raise ValidationError("No se puede cancelar una orden ya recibida o ya cancelada.")

        self.status = self.STATUS_CANCELLED
        self.received_by = None
        self.received_at = None

        self.full_clean()
        self.save(update_fields=["status", "received_by", "received_at", "updated_at"])


class PurchaseOrderLine(models.Model):
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()

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

        if self.purchase_order_id:
            po = getattr(self, "purchase_order", None)
            po_status = None
            if po is not None and getattr(po, "status", None) is not None:
                po_status = po.status
            else:
                po_status = PurchaseOrder.objects.filter(pk=self.purchase_order_id).values_list("status", flat=True).first()

            if po_status and po_status != PurchaseOrder.STATUS_DRAFT:
                raise ValidationError("No se pueden modificar líneas si la orden no está en DRAFT.")
