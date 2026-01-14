from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Q, Sum
from django.utils import timezone


class Product(models.Model):
    # ===============================
    # Campos base
    # ===============================
    sku = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
        help_text="Código único del producto (SKU). Idealmente coincide con el número impreso bajo el código de barras del fabricante.",
    )

    # ✅ Código interno (alfanumérico) definido por el usuario
    internal_code = models.CharField(
        max_length=50,
        blank=True,
        default="",
        db_index=True,
        help_text="Código interno opcional definido por la empresa (alfanumérico).",
    )

    name = models.CharField(
        max_length=255,
        help_text="Nombre del producto",
    )

    description = models.TextField(
        blank=True,
        help_text="Descripción opcional",
    )

    # ===============================
    # Stock materializado (fuente operativa)
    # ===============================
    # Nota: en UI NO se carga manualmente.
    stock = models.IntegerField(
        default=0,
        help_text="Cantidad actual en stock (no puede ser negativa). Se actualiza por movimientos IN/OUT.",
    )

    # ===============================
    # Costos / precios
    # ===============================
    # ✅ Costo unitario de compra (ya existe y lo usan compras)
    purchase_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Costo unitario de compra (>= 0). Se usa en Órdenes de Compra.",
    )

    # ✅ Precio de venta
    sale_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Precio de venta (>= 0).",
    )

    # ===============================
    # Unidad de medida
    # ===============================
    UOM_UNIT = "UNIT"
    UOM_LITER = "LITER"
    UOM_KILO = "KILO"
    UOM_CHOICES = (
        (UOM_UNIT, "Unidad"),
        (UOM_LITER, "Litro"),
        (UOM_KILO, "Kilo"),
    )

    unit_of_measure = models.CharField(
        max_length=10,
        choices=UOM_CHOICES,
        default=UOM_UNIT,
        db_index=True,
        help_text="Unidad de medida del producto.",
    )

    # ===============================
    # Impuestos (Argentina)
    # ===============================
    TAX_IVA_21 = "IVA_21"
    TAX_IVA_105 = "IVA_105"
    TAX_IVA_27 = "IVA_27"
    TAX_EXEMPT = "EXEMPT"
    TAX_NOT_TAXED = "NOT_TAXED"
    TAX_CHOICES = (
        (TAX_IVA_21, "IVA 21%"),
        (TAX_IVA_105, "IVA 10.5%"),
        (TAX_IVA_27, "IVA 27%"),
        (TAX_EXEMPT, "Exento"),
        (TAX_NOT_TAXED, "No gravado"),
    )

    tax_type = models.CharField(
        max_length=20,
        choices=TAX_CHOICES,
        default=TAX_IVA_21,
        db_index=True,
        help_text="Tipo de impuesto aplicable.",
    )

    # Valor del impuesto en porcentaje (ej: 21.00)
    tax_rate = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("21.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Valor del impuesto (%) para el producto. Ej: 21.00",
    )

    # ===============================
    # Clasificación
    # ===============================
    category = models.CharField(
        max_length=120,
        blank=True,
        default="",
        db_index=True,
        help_text="Categoría del producto (texto).",
    )

    brand = models.CharField(
        max_length=120,
        blank=True,
        default="",
        db_index=True,
        help_text="Marca del producto (texto).",
    )

    # ===============================
    # Estado
    # ===============================
    STATUS_ACTIVE = "ACTIVE"
    STATUS_INACTIVE = "INACTIVE"
    STATUS_CHOICES = (
        (STATUS_ACTIVE, "Activo"),
        (STATUS_INACTIVE, "Inactivo"),
    )

    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
        db_index=True,
        help_text="Estado operativo del producto.",
    )

    # ✅ Mantener compatibilidad con lo ya existente (se usa en queries y en autocomplete)
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Compatibilidad: refleja el estado activo/inactivo.",
    )

    # ===============================
    # Barcode / QR
    # ===============================
    barcode_value = models.CharField(
        max_length=120,
        blank=True,
        default="",
        db_index=True,
        help_text="Valor del código de barras. Se autogenera desde SKU.",
    )

    qr_payload = models.TextField(
        blank=True,
        default="",
        help_text="Payload del QR (texto). Se autogenera desde datos del producto.",
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
            models.CheckConstraint(
                name="stock_product_purchase_cost_non_negative",
                check=Q(purchase_cost__gte=0),
            ),
            models.CheckConstraint(
                name="stock_product_sale_price_non_negative",
                check=Q(sale_price__gte=0),
            ),
            models.CheckConstraint(
                name="stock_product_tax_rate_non_negative",
                check=Q(tax_rate__gte=0),
            ),
        ]

    def __str__(self):
        return f"{self.sku} - {self.name}"

    # ===============================
    # Normalización / sincronización
    # ===============================

    def clean(self):
        # Normalizaciones suaves
        if self.sku:
            self.sku = self.sku.strip()
        if self.internal_code:
            self.internal_code = self.internal_code.strip()
        if self.name:
            self.name = self.name.strip()
        if self.category:
            self.category = self.category.strip()
        if self.brand:
            self.brand = self.brand.strip()

        # Sync estado → is_active
        if self.status == self.STATUS_ACTIVE:
            self.is_active = True
        elif self.status == self.STATUS_INACTIVE:
            self.is_active = False
        else:
            raise ValidationError({"status": "Estado inválido."})

        # barcode desde SKU (siempre)
        self.barcode_value = (self.sku or "").strip()

        # qr payload desde campos requeridos por el usuario
        self.qr_payload = self.build_qr_payload()

        # Validación de internal_code: opcional, pero si existe lo queremos “usable”
        # (no forzamos unique aquí porque lo define la DB / constraint si lo agregamos)
        if self.internal_code and len(self.internal_code) < 2:
            raise ValidationError({"internal_code": "El código interno debe tener al menos 2 caracteres."})

    def build_qr_payload(self) -> str:
        """
        QR basado en:
        id, sku, name, description, barcode_value, unit_of_measure, brand, category, tax_type, tax_rate, prices.
        Nota: id puede no existir antes de guardar. Se agrega si ya existe.
        """
        data = {
            "product_id": self.pk or None,
            "sku": (self.sku or "").strip(),
            "internal_code": (self.internal_code or "").strip(),
            "name": (self.name or "").strip(),
            "description": (self.description or "").strip(),
            "barcode_value": (self.sku or "").strip(),
            "unit_of_measure": self.unit_of_measure,
            "brand": (self.brand or "").strip(),
            "category": (self.category or "").strip(),
            "tax_type": self.tax_type,
            "tax_rate": str(self.tax_rate) if self.tax_rate is not None else "",
            "purchase_cost": str(self.purchase_cost) if self.purchase_cost is not None else "",
            "sale_price": str(self.sale_price) if self.sale_price is not None else "",
            "status": self.status,
        }

        # JSON compacto (legible, sin romper caracteres)
        import json
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    def save(self, *args, **kwargs):
        # clean() también sincroniza barcode_value, qr_payload y status/is_active
        self.full_clean()
        return super().save(*args, **kwargs)

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

    def clean(self):
        if not self.product_id:
            raise ValidationError({"product": "Debe indicar un producto."})

        if self.quantity is None or int(self.quantity) <= 0:
            raise ValidationError({"quantity": "La cantidad debe ser mayor a 0."})

        if self.movement_type not in {self.IN, self.OUT}:
            raise ValidationError({"movement_type": "Tipo de movimiento inválido."})

        if not Product.objects.filter(pk=self.product_id, is_active=True).exists():
            raise ValidationError({"product": "El producto está inactivo."})

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
