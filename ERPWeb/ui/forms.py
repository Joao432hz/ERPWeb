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

    # ✅ IMPORTANTE:
    # - required=False para permitir filas vacías sin que el formset explote
    # - validamos cantidad SOLO si hay product_id
    quantity = forms.IntegerField(label="Cantidad", required=False, min_value=1)

    # storage invisible (debug)
    unit_cost_display = forms.CharField(required=False, widget=forms.HiddenInput())

    def clean(self):
        cleaned = super().clean()

        pid = cleaned.get("product_id")
        qty = cleaned.get("quantity")

        # Si el usuario escribió algo pero no seleccionó desde sugerencias
        # (en general product_id vacío) => error SOLO si intentó cargar la fila.
        # Heurística: si hay texto en product_query o qty cargada.
        pq = (cleaned.get("product_query") or "").strip()
        user_touched_row = bool(pq) or (qty is not None)

        if user_touched_row and not pid:
            raise forms.ValidationError("Seleccioná un producto (desde las sugerencias).")

        # Si hay producto, cantidad es obligatoria y > 0
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
