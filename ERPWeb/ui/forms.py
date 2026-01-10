from django import forms
from django.forms import BaseFormSet, formset_factory


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
    quantity = forms.IntegerField(label="Cantidad", min_value=1)

    # ✅ lo dejamos solo como “storage” invisible (por si querés debug),
    # pero NO se muestra como recuadro en la UI.
    unit_cost_display = forms.CharField(required=False, widget=forms.HiddenInput())

    def clean_product_id(self):
        pid = self.cleaned_data.get("product_id")
        if not pid:
            raise forms.ValidationError("Seleccioná un producto (desde las sugerencias).")
        return pid


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
            if cd.get("product_id") and cd.get("quantity"):
                valid_rows += 1
        if valid_rows <= 0:
            raise forms.ValidationError("Cargá al menos 1 línea válida.")


PurchaseOrderLineFormSet = formset_factory(
    PurchaseOrderLineForm,
    formset=_BaseLineFormSet,
    extra=1,
    can_delete=True,
)
