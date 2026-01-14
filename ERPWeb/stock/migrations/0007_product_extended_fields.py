from decimal import Decimal
from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ("stock", "0006_product_purchase_cost_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="internal_code",
            field=models.CharField(
                blank=True,
                default="",
                db_index=True,
                help_text="Código interno opcional definido por la empresa (alfanumérico).",
                max_length=50,
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="sale_price",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                help_text="Precio de venta (>= 0).",
                max_digits=12,
                validators=[django.core.validators.MinValueValidator(Decimal("0.00"))],
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="unit_of_measure",
            field=models.CharField(
                choices=[("UNIT", "Unidad"), ("LITER", "Litro"), ("KILO", "Kilo")],
                db_index=True,
                default="UNIT",
                help_text="Unidad de medida del producto.",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="tax_type",
            field=models.CharField(
                choices=[
                    ("IVA_21", "IVA 21%"),
                    ("IVA_105", "IVA 10.5%"),
                    ("IVA_27", "IVA 27%"),
                    ("EXEMPT", "Exento"),
                    ("NOT_TAXED", "No gravado"),
                ],
                db_index=True,
                default="IVA_21",
                help_text="Tipo de impuesto aplicable.",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="tax_rate",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("21.00"),
                help_text="Valor del impuesto (%) para el producto. Ej: 21.00",
                max_digits=6,
                validators=[django.core.validators.MinValueValidator(Decimal("0.00"))],
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="category",
            field=models.CharField(
                blank=True,
                default="",
                db_index=True,
                help_text="Categoría del producto (texto).",
                max_length=120,
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="brand",
            field=models.CharField(
                blank=True,
                default="",
                db_index=True,
                help_text="Marca del producto (texto).",
                max_length=120,
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="status",
            field=models.CharField(
                choices=[("ACTIVE", "Activo"), ("INACTIVE", "Inactivo")],
                db_index=True,
                default="ACTIVE",
                help_text="Estado operativo del producto.",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="barcode_value",
            field=models.CharField(
                blank=True,
                default="",
                db_index=True,
                help_text="Valor del código de barras. Se autogenera desde SKU.",
                max_length=120,
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="qr_payload",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Payload del QR (texto). Se autogenera desde datos del producto.",
            ),
        ),
    ]
