from decimal import Decimal
from django import forms
from django.core.exceptions import ValidationError

from stock.models import Product


class ProductCreateForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            "sku",
            "internal_code",
            "name",
            "description",
            "unit_of_measure",
            "purchase_cost",
            "sale_price",
            "tax_type",
            "tax_rate",
            "category",
            "brand",
            "status",
        ]
        widgets = {
            "sku": forms.TextInput(attrs={"class": "form-control", "placeholder": "SKU (como en el producto real)"}),
            "internal_code": forms.TextInput(attrs={"class": "form-control", "placeholder": "Código interno (opcional)"}),
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Nombre del producto"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Descripción"}),
            "unit_of_measure": forms.Select(attrs={"class": "form-select"}),
            "purchase_cost": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "sale_price": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "tax_type": forms.Select(attrs={"class": "form-select"}),
            "tax_rate": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "category": forms.TextInput(attrs={"class": "form-control", "placeholder": "Categoría (texto)"}),
            "brand": forms.TextInput(attrs={"class": "form-control", "placeholder": "Marca (texto)"}),
            "status": forms.Select(attrs={"class": "form-select"}),
        }

    def clean_sku(self):
        sku = (self.cleaned_data.get("sku") or "").strip()
        if not sku:
            raise ValidationError("El SKU es obligatorio.")
        return sku

    def clean_purchase_cost(self):
        v = self.cleaned_data.get("purchase_cost")
        if v is None:
            return Decimal("0.00")
        if v < 0:
            raise ValidationError("El costo unitario no puede ser negativo.")
        return v

    def clean_sale_price(self):
        v = self.cleaned_data.get("sale_price")
        if v is None:
            return Decimal("0.00")
        if v < 0:
            raise ValidationError("El precio de venta no puede ser negativo.")
        return v

    def clean_tax_rate(self):
        v = self.cleaned_data.get("tax_rate")
        if v is None:
            return Decimal("0.00")
        if v < 0:
            raise ValidationError("El valor del impuesto no puede ser negativo.")
        return v


class ProductEditForm(forms.ModelForm):
    """
    Editar producto:
    - Mantiene ID (instance)
    - Permite upload manual de imagen
    - Permite quitar imagen (remove_image)
    """
    remove_image = forms.BooleanField(
        required=False,
        label="Quitar imagen",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    class Meta:
        model = Product
        fields = [
            "sku",
            "internal_code",
            "name",
            "description",
            "unit_of_measure",
            "purchase_cost",
            "sale_price",
            "tax_type",
            "tax_rate",
            "category",
            "brand",
            "status",
            "image",  # ✅ upload manual
        ]
        widgets = {
            "sku": forms.TextInput(attrs={"class": "form-control", "placeholder": "SKU (como en el producto real)"}),
            "internal_code": forms.TextInput(attrs={"class": "form-control", "placeholder": "Código interno (opcional)"}),
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Nombre del producto"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Descripción"}),
            "unit_of_measure": forms.Select(attrs={"class": "form-select"}),
            "purchase_cost": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "sale_price": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "tax_type": forms.Select(attrs={"class": "form-select"}),
            "tax_rate": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "category": forms.TextInput(attrs={"class": "form-control", "placeholder": "Categoría (texto)"}),
            "brand": forms.TextInput(attrs={"class": "form-control", "placeholder": "Marca (texto)"}),
            "status": forms.Select(attrs={"class": "form-select"}),
            "image": forms.ClearableFileInput(attrs={"class": "form-control"}),
        }

    def clean_sku(self):
        sku = (self.cleaned_data.get("sku") or "").strip()
        if not sku:
            raise ValidationError("El SKU es obligatorio.")
        return sku

    def clean_purchase_cost(self):
        v = self.cleaned_data.get("purchase_cost")
        if v is None:
            return Decimal("0.00")
        if v < 0:
            raise ValidationError("El costo unitario no puede ser negativo.")
        return v

    def clean_sale_price(self):
        v = self.cleaned_data.get("sale_price")
        if v is None:
            return Decimal("0.00")
        if v < 0:
            raise ValidationError("El precio de venta no puede ser negativo.")
        return v

    def clean_tax_rate(self):
        v = self.cleaned_data.get("tax_rate")
        if v is None:
            return Decimal("0.00")
        if v < 0:
            raise ValidationError("El valor del impuesto no puede ser negativo.")
        return v
