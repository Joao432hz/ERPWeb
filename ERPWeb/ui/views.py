from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.db.models import Q
from django.views.decorators.http import require_POST
from django.db import transaction
from django.contrib import messages

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

    from purchases.models import PurchaseOrder  # ya sabemos que existe si llegamos acá normalmente

    po = get_object_or_404(
        PurchaseOrder.objects.select_related("supplier", "created_by", "confirmed_by", "received_by")
        .prefetch_related("lines__product"),
        pk=pk,
    )

    context.update(
        {
            "po": po,
            "lines": list(po.lines.all()),
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
