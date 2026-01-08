import json

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.core.exceptions import ValidationError
from django.contrib.auth.decorators import login_required

from security.decorators import require_permission
from .models import Product, StockMovement


def _json_body(request):
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return {}


@login_required
@require_permission("stock.product.view")
@require_http_methods(["GET"])
def products_list(request):
    qs = Product.objects.all().order_by("name")
    data = [
        {
            "id": p.id,
            "sku": p.sku,
            "name": p.name,
            "stock": p.stock,
            "updated_at": p.updated_at.isoformat(),
        }
        for p in qs
    ]
    return JsonResponse({"count": len(data), "results": data})


@login_required
@require_permission("stock.movement.view")
@require_http_methods(["GET"])
def movements_list(request):
    qs = (
        StockMovement.objects.select_related("product", "created_by")
        .all()
        .order_by("-created_at")[:200]
    )
    data = [
        {
            "id": m.id,
            "product_id": m.product_id,
            "product_sku": m.product.sku,
            "movement_type": m.movement_type,
            "quantity": m.quantity,
            "note": m.note,
            "created_by": getattr(m.created_by, "username", None),
            "created_at": m.created_at.isoformat(),
        }
        for m in qs
    ]
    return JsonResponse({"count": len(data), "results": data})


@login_required
@require_permission("stock.movement.create")
@require_http_methods(["POST"])
@csrf_exempt  # Technical API (JSON) - CSRF intentionally disabled per API_RULES.md
def movement_create(request):
    body = _json_body(request)

    product_id = body.get("product_id")
    movement_type = body.get("movement_type")  # "IN" o "OUT"
    quantity = body.get("quantity")
    note = body.get("note", "")

    if not product_id or movement_type not in ("IN", "OUT") or quantity is None:
        return JsonResponse(
            {"detail": "Campos requeridos: product_id, movement_type(IN/OUT), quantity"},
            status=400,
        )

    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        return JsonResponse({"detail": "quantity debe ser entero"}, status=400)

    if quantity <= 0:
        return JsonResponse({"detail": "quantity debe ser > 0"}, status=400)

    # Creamos el movimiento. El save() aplica la lógica transaccional y valida stock negativo.
    try:
        movement = StockMovement(
            product_id=product_id,
            movement_type=movement_type,
            quantity=quantity,
            note=note,
            created_by=request.user,
        )
        movement.save()

    except ValidationError as e:
        if hasattr(e, "message_dict"):
            return JsonResponse({"detail": e.message_dict}, status=400)
        if hasattr(e, "messages"):
            return JsonResponse({"detail": e.messages}, status=400)
        return JsonResponse({"detail": str(e)}, status=400)

    except Exception as e:
        return JsonResponse({"detail": str(e)}, status=400)

    return JsonResponse(
        {
            "status": "ok",
            "movement_id": movement.id,
            "product_id": movement.product_id,
            "movement_type": movement.movement_type,
            "quantity": movement.quantity,
        }
    )
