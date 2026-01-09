from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.db.models import Q

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


@login_required
def dashboard(request):
    perm_keys = _user_perm_keys(request.user)

    context = {
        "perm_keys": perm_keys,
        "can_stock_products": (
            request.user.is_superuser or "stock.product.view" in perm_keys
        ),
        "can_stock_movements": (
            request.user.is_superuser or "stock.movement.view" in perm_keys
        ),
        "can_purchases": (
            request.user.is_superuser or "purchases.order.view" in perm_keys
        ),
        "can_sales": (
            request.user.is_superuser or "sales.order.view" in perm_keys
        ),
        "can_finance": (
            request.user.is_superuser or "finance.movement.view" in perm_keys
        ),
    }

    return render(request, "ui/dashboard.html", context)


@login_required
def forbidden(request):
    return render(request, "ui/forbidden.html", status=403)


@login_required
def stock_products(request):
    perm_keys = _user_perm_keys(request.user)
    if not (request.user.is_superuser or "stock.product.view" in perm_keys):
        return render(request, "ui/forbidden.html", status=403)

    q = (request.GET.get("q") or "").strip()
    qs = Product.objects.all().order_by("name")
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(sku__icontains=q))

    return render(
        request,
        "ui/stock_products.html",
        {
            "products": qs,
            "q": q,
            "can_stock_products": True,
        },
    )


@login_required
def stock_movements(request):
    perm_keys = _user_perm_keys(request.user)
    if not (request.user.is_superuser or "stock.movement.view" in perm_keys):
        return render(request, "ui/forbidden.html", status=403)

    qs = StockMovement.objects.select_related("product").order_by("-created_at")[:200]
    return render(
        request,
        "ui/stock_movements.html",
        {
            "movements": qs,
            "can_stock_movements": True,
        },
    )
