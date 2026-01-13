from django import forms
from django.forms import BaseFormSet, formset_factory

from purchases.models import Supplier


class PurchaseOrderCreateForm(forms.Form):
    supplier = forms.ChoiceField(label="Proveedor", choices=())
    note = forms.CharField(label="Nota", required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, suppliers_qs=None, **kwargs):
        super().__init__(*args, **kwargs)
        suppliers_qs = suppliers_qs or []
        self.fields["supplier"].choices = [("", "— Seleccionar —")] + [(str(s.id), s.name) for s in suppliers_qs]

    def clean_supplier(self):
        raw = (self.cleaned_data.get("supplier") or "").strip()
        if not raw:
            raise forms.ValidationError("Seleccioná un proveedor.")
        try:
            return int(raw)
        except ValueError:
            raise forms.ValidationError("Proveedor inválido.")

    def clean(self):
        cleaned = super().clean()
        if "supplier" in cleaned:
            self.cleaned_data["supplier_id"] = cleaned["supplier"]
        return cleaned


class PurchaseOrderLineForm(forms.Form):
    product_query = forms.CharField(label="Producto", required=False)
    product_id = forms.IntegerField(required=False, widget=forms.HiddenInput())

    quantity = forms.IntegerField(label="Cantidad", required=False, min_value=1)
    unit_cost_display = forms.CharField(required=False, widget=forms.HiddenInput())

    def clean(self):
        cleaned = super().clean()

        pid = cleaned.get("product_id")
        qty = cleaned.get("quantity")

        pq = (cleaned.get("product_query") or "").strip()
        user_touched_row = bool(pq) or (qty is not None)

        if user_touched_row and not pid:
            raise forms.ValidationError("Seleccioná un producto (desde las sugerencias).")

        if pid and (qty is None or int(qty) <= 0):
            raise forms.ValidationError("Ingresá una cantidad válida (>= 1).")

        return cleaned


class _BaseLineFormSet(BaseFormSet):
    def clean(self):
        super().clean()
        valid_rows = 0

        for f in self.forms:
            if not hasattr(f, "cleaned_data"):
                continue
            cd = f.cleaned_data or {}
            if cd.get("DELETE"):
                continue

            pid = cd.get("product_id")
            qty = cd.get("quantity")

            if pid and qty:
                valid_rows += 1

        if valid_rows <= 0:
            raise forms.ValidationError("Cargá al menos 1 línea válida.")


PurchaseOrderLineFormSet = formset_factory(
    PurchaseOrderLineForm,
    formset=_BaseLineFormSet,
    extra=1,
    can_delete=True,
)


# ============================================================
# ✅ SUPPLIER CREATE / EDIT
# ============================================================

PAYMENT_TERMS_CHOICES = [
    ("CONTADO", "Contado"),
    ("ANTICIPO", "Con anticipo"),
    ("30", "30 días"),
    ("60", "60 días"),
    ("90", "90 días"),
]


class MultiFileInput(forms.ClearableFileInput):
    # ✅ Habilita múltiples archivos en Django sin tirar ValueError
    allow_multiple_selected = True


class SupplierCreateForm(forms.ModelForm):
    payment_terms = forms.MultipleChoiceField(
        label="Condiciones de pago",
        choices=PAYMENT_TERMS_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    standard_payment_terms = forms.MultipleChoiceField(
        label="Plazo de pago estándar",
        choices=PAYMENT_TERMS_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    documents = forms.FileField(
        label="Documentos anexos",
        required=False,
        widget=MultiFileInput(attrs={"multiple": True, "accept": ".pdf,.doc,.docx,.jpg,.jpeg,.bmp,.png"}),
    )

    extra_fields_text = forms.CharField(
        label="Campos adicionales (JSON key/value)",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": '{"Ejemplo": "Valor", "Otro": "123"}'}),
    )

    class Meta:
        model = Supplier
        fields = [
            "name",
            "trade_name",
            "supplier_type",
            "vat_condition",
            "tax_id",
            "document_type",

            "fiscal_address",
            "province",
            "postal_code",
            "country",

            "phone",
            "phone_secondary",
            "email",
            "email_ap",
            "contact_name",
            "contact_role",
            "fax_or_web",

            "payment_terms",
            "standard_payment_terms",
            "price_list_update_days",
            "transaction_currency",
            "account_reference",
            "classification",
            "product_category",

            "bank_name",
            "bank_account_ref",
            "bank_account_type",
            "bank_account_holder",
            "bank_account_currency",

            "tax_condition",
            "retention_category",
            "retention_codes",

            "status",
            "internal_notes",
        ]

        widgets = {
            "internal_notes": forms.Textarea(attrs={"rows": 3}),
            "fiscal_address": forms.TextInput(attrs={"placeholder": "Calle, número, piso, ciudad"}),
        }

    def clean_extra_fields_text(self):
        import json
        raw = (self.cleaned_data.get("extra_fields_text") or "").strip()
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except Exception:
            raise forms.ValidationError('JSON inválido. Ej: {"Clave": "Valor"}')
        if not isinstance(data, dict):
            raise forms.ValidationError("Debe ser un JSON objeto (key/value).")
        return data

    def save(self, commit=True):
        obj: Supplier = super().save(commit=False)
        obj.extra_fields = self.cleaned_data.get("extra_fields_text") or {}
        if commit:
            obj.save()
        return obj
