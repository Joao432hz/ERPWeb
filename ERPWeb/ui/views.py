from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST, require_http_methods

from security.models import RolePermission
from stock.models import Product, StockMovement


def _user_perm_keys(user):
    if not user or not user.is_authenticated:
        return set()
    if user.is_superuser:
        return {"*"}
    return set(
        RolePermission.objects.filter(
            role__userrole__user=user,
            role__is_active=True,
        ).values_list("permission__code", flat=True)
    )


def _base_context(user):
    perm_keys = _user_perm_keys(user)
    is_super = bool(getattr(user, "is_superuser", False))

    return {
        "perm_keys": perm_keys,

        # Sidebar gates
        "can_stock_products": (is_super or "stock.product.view" in perm_keys),
        "can_stock_movements": (is_super or "stock.movement.view" in perm_keys),

        "can_purchases": (is_super or "purchases.order.view" in perm_keys),
        "can_sales": (is_super or "sales.order.view" in perm_keys),
        "can_finance": (is_super or "finance.movement.view" in perm_keys),

        # Compras actions (para botones)
        "can_purchases_create": (is_super or "purchases.order.create" in perm_keys),
        "can_purchases_confirm": (is_super or "purchases.order.confirm" in perm_keys),
        "can_purchases_receive": (is_super or "purchases.order.receive" in perm_keys),
        "can_purchases_cancel": (is_super or "purchases.order.cancel" in perm_keys),
    }


def _forbidden(request, required_permission=None):
    ctx = _base_context(request.user)
    if required_permission:
        ctx["required_permission"] = required_permission
    return render(request, "ui/forbidden.html", ctx, status=403)


def _has_perm(request, code: str) -> bool:
    if getattr(request.user, "is_superuser", False):
        return True
    perm_keys = _user_perm_keys(request.user)
    return code in perm_keys


def _as_decimal(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _money_str(val: Decimal) -> str:
    if val is None:
        return ""
    q = val.quantize(Decimal("0.01"))
    return f"{q:.2f}"


def _product_purchase_cost(product: Product) -> Decimal:
    # Campo ya agregado a Product
    val = _as_decimal(getattr(product, "purchase_cost", None))
    if val is None:
        raise ValueError("El producto no tiene purchase_cost válido.")
    return val


def _po_line_fk_name(PurchaseOrderLine, PurchaseOrder) -> str:
    """
    Detecta el nombre real del ForeignKey desde PurchaseOrderLine -> PurchaseOrder.
    Evita asumir 'order' / 'purchase_order' etc.
    """
    for f in PurchaseOrderLine._meta.fields:
        rel = getattr(f, "remote_field", None)
        if rel and getattr(rel, "model", None) == PurchaseOrder:
            return f.name
    raise ValueError("No se encontró FK desde PurchaseOrderLine hacia PurchaseOrder.")


@login_required
def dashboard(request):
    context = _base_context(request.user)
    return render(request, "ui/dashboard.html", context)


@login_required
def forbidden(request):
    context = _base_context(request.user)
    return render(request, "ui/forbidden.html", context, status=403)


@login_required
def stock_products(request):
    context = _base_context(request.user)
    if not _has_perm(request, "stock.product.view"):
        return _forbidden(request, required_permission="stock.product.view")

    q = (request.GET.get("q") or "").strip()
    qs = Product.objects.all().order_by("name")
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(sku__icontains=q))

    context.update({"products": qs, "q": q})
    return render(request, "ui/stock_products.html", context)


@login_required
def stock_movements(request):
    context = _base_context(request.user)
    if not _has_perm(request, "stock.movement.view"):
        return _forbidden(request, required_permission="stock.movement.view")

    qs = StockMovement.objects.select_related("product").order_by("-created_at")[:200]
    context.update({"movements": qs})
    return render(request, "ui/stock_movements.html", context)


@login_required
def purchases_orders(request):
    context = _base_context(request.user)
    if not _has_perm(request, "purchases.order.view"):
        return _forbidden(request, required_permission="purchases.order.view")

    try:
        from purchases.models import PurchaseOrder
    except Exception:
        context.update({"module_name": "Compras", "detail": "No se pudo importar purchases.models.PurchaseOrder"})
        return render(request, "ui/not_available.html", context, status=500)

    q = (request.GET.get("q") or "").strip()
    qs = PurchaseOrder.objects.select_related("supplier").all().order_by("-id")
    if q:
        qs = qs.filter(Q(id__icontains=q) | Q(supplier__name__icontains=q))

    context.update({"orders": qs[:200], "q": q})
    return render(request, "ui/purchases_orders.html", context)


@login_required
def purchases_order_detail(request, pk: int):
    context = _base_context(request.user)
    if not _has_perm(request, "purchases.order.view"):
        return _forbidden(request, required_permission="purchases.order.view")

    from purchases.models import PurchaseOrder

    po = get_object_or_404(
        PurchaseOrder.objects.select_related("supplier", "created_by", "confirmed_by", "received_by")
        .prefetch_related("lines__product"),
        pk=pk,
    )

    lines = list(po.lines.all())

    # ✅ Totales: línea y orden (Decimal safe)
    po_total = Decimal("0.00")
    line_items = []
    for ln in lines:
        qty = _as_decimal(getattr(ln, "quantity", None)) or Decimal("0")
        unit = _as_decimal(getattr(ln, "unit_cost", None)) or Decimal("0")
        line_total = (qty * unit).quantize(Decimal("0.01"))
        po_total += line_total
        line_items.append(
            {
                "line": ln,
                "line_total": line_total,
                "line_total_str": _money_str(line_total),
            }
        )

    po_total = po_total.quantize(Decimal("0.01"))

    context.update(
        {
            "po": po,
            "lines": lines,  # mantengo compatibilidad
            "line_items": line_items,
            "po_total": po_total,
            "po_total_str": _money_str(po_total),
        }
    )
    return render(request, "ui/purchases_order_detail.html", context)


@require_POST
@login_required
def purchases_order_confirm(request, pk: int):
    if not _has_perm(request, "purchases.order.confirm"):
        return _forbidden(request, required_permission="purchases.order.confirm")

    from purchases.models import PurchaseOrder

    with transaction.atomic():
        po = get_object_or_404(PurchaseOrder.objects.select_for_update(), pk=pk)
        try:
            po.confirm(request.user)
            messages.success(request, f"PO#{po.id} confirmada correctamente.")
        except Exception as e:
            messages.error(request, f"No se pudo confirmar PO#{pk}: {e}")

    return redirect("ui:purchases_order_detail", pk=pk)


@require_POST
@login_required
def purchases_order_receive(request, pk: int):
    if not _has_perm(request, "purchases.order.receive"):
        return _forbidden(request, required_permission="purchases.order.receive")

    from purchases.models import PurchaseOrder

    with transaction.atomic():
        po = get_object_or_404(PurchaseOrder.objects.select_for_update(), pk=pk)
        try:
            po.receive(request.user)
            messages.success(request, f"PO#{po.id} recibida. Stock impactado y payable generado (si aplica).")
        except Exception as e:
            messages.error(request, f"No se pudo recibir PO#{pk}: {e}")

    return redirect("ui:purchases_order_detail", pk=pk)


@require_POST
@login_required
def purchases_order_cancel(request, pk: int):
    if not _has_perm(request, "purchases.order.cancel"):
        return _forbidden(request, required_permission="purchases.order.cancel")

    from purchases.models import PurchaseOrder

    with transaction.atomic():
        po = get_object_or_404(PurchaseOrder.objects.select_for_update(), pk=pk)
        try:
            po.cancel(request.user)
            messages.success(request, f"PO#{po.id} cancelada correctamente.")
        except Exception as e:
            messages.error(request, f"No se pudo cancelar PO#{pk}: {e}")

    return redirect("ui:purchases_order_detail", pk=pk)


# ===============================
# ✅ API UI: Autocomplete Products
# ===============================

@login_required
@require_http_methods(["GET"])
def products_search(request):
    q = (request.GET.get("q") or "").strip()
    if len(q) < 2:
        return JsonResponse({"items": []})

    qs = (
        Product.objects.filter(is_active=True)
        .filter(Q(name__icontains=q) | Q(sku__icontains=q))
        .order_by("name")[:10]
    )

    items = []
    for p in qs:
        try:
            cost = _product_purchase_cost(p)
            cost_str = _money_str(cost)
        except Exception:
            cost_str = None

        items.append(
            {
                "id": p.id,
                "label": f"{p.name} ({p.sku})",
                "sku": p.sku,
                "cost": cost_str,
            }
        )
    return JsonResponse({"items": items})


@login_required
@require_http_methods(["GET"])
def product_detail(request, pk: int):
    p = get_object_or_404(Product, pk=pk, is_active=True)
    try:
        cost = _money_str(_product_purchase_cost(p))
    except Exception:
        cost = None
    return JsonResponse(
        {
            "id": p.id,
            "label": f"{p.name} ({p.sku})",
            "sku": p.sku,
            "cost": cost,
        }
    )


# ===============================
# ✅ UI: Crear OC (Nueva OC)
# ===============================

@login_required
@require_http_methods(["GET", "POST"])
def purchases_order_create(request):
    if not _has_perm(request, "purchases.order.create"):
        return _forbidden(request, required_permission="purchases.order.create")

    context = _base_context(request.user)

    from purchases.models import Supplier, PurchaseOrder, PurchaseOrderLine
    from ui.forms import PurchaseOrderCreateForm, PurchaseOrderLineFormSet

    suppliers = Supplier.objects.filter(is_active=True).order_by("name")
    form = PurchaseOrderCreateForm(
        data=request.POST or None,
        suppliers_qs=suppliers,
    )
    formset = PurchaseOrderLineFormSet(request.POST or None, prefix="form")

    if request.method == "POST":
        if form.is_valid() and formset.is_valid():
            supplier_id = form.cleaned_data["supplier_id"]
            note = (form.cleaned_data.get("note") or "").strip()

            try:
                with transaction.atomic():
                    po = PurchaseOrder.objects.create(
                        supplier_id=supplier_id,
                        note=note,
                        created_by=request.user,
                    )

                    fk_name = _po_line_fk_name(PurchaseOrderLine, PurchaseOrder)

                    for f in formset.forms:
                        cd = f.cleaned_data or {}
                        if cd.get("DELETE"):
                            continue

                        product_id = cd.get("product_id")
                        qty = cd.get("quantity")

                        if not product_id or not qty:
                            continue

                        product = Product.objects.get(pk=product_id, is_active=True)
                        unit_cost = _product_purchase_cost(product)

                        if unit_cost <= 0:
                            raise ValueError(f"El producto {product.sku} no tiene costo de compra (> 0).")

                        kwargs = {
                            fk_name: po,
                            "product": product,
                            "quantity": qty,
                            "unit_cost": unit_cost,
                        }
                        PurchaseOrderLine.objects.create(**kwargs)

                messages.success(request, f"OC creada en DRAFT: PO#{po.id}")
                return redirect("ui:purchases_orders")

            except Exception as e:
                messages.error(request, f"No se pudo crear la OC: {e}")

        else:
            messages.error(request, "Revisá los errores del formulario.")

    context.update(
        {
            "form": form,
            "formset": formset,
        }
    )
    return render(request, "ui/purchases_order_create.html", context)


# ======= Mantengo placeholders existentes (Ventas/Finanzas) =======

@login_required
def sales_orders(request):
    context = _base_context(request.user)
    if not _has_perm(request, "sales.order.view"):
        return _forbidden(request, required_permission="sales.order.view")

    try:
        from sales.models import SalesOrder
    except Exception:
        context.update({"module_name": "Ventas", "detail": "No se pudo importar sales.models.SalesOrder"})
        return render(request, "ui/not_available.html", context, status=500)

    q = (request.GET.get("q") or "").strip()
    qs = SalesOrder.objects.all().order_by("-id")
    if q:
        qs = qs.filter(Q(id__icontains=q) | Q(customer_name__icontains=q))

    context.update({"orders": qs[:200], "q": q})
    return render(request, "ui/sales_orders.html", context)


@login_required
def finance_movements(request):
    context = _base_context(request.user)
    if not _has_perm(request, "finance.movement.view"):
        return _forbidden(request, required_permission="finance.movement.view")

    try:
        from finance.models import FinancialMovement
    except Exception:
        context.update({"module_name": "Finanzas", "detail": "No se pudo importar finance.models.FinancialMovement"})
        return render(request, "ui/not_available.html", context, status=500)

    q = (request.GET.get("q") or "").strip()
    qs = FinancialMovement.objects.all().order_by("-created_at")
    if q:
        qs = qs.filter(Q(id__icontains=q) | Q(source_type__icontains=q) | Q(source_id__icontains=q))

    context.update({"movements": qs[:200], "q": q})
    return render(request, "ui/finance_movements.html", context)
