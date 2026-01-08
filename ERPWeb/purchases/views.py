import json
from decimal import Decimal, InvalidOperation

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError

from security.decorators import require_permission
from .models import Supplier, PurchaseOrder, PurchaseOrderLine
from stock.models import Product


DEC_0 = Decimal("0.00")


def _json_body(request):
    """
    Lee JSON del body de forma robusta.
    Si viene vacío o inválido -> {}.
    """
    try:
        raw = request.body.decode("utf-8") if request.body else ""
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def _bad_request(msg, status=400):
    return JsonResponse({"detail": msg}, status=status)


def _validation_error_response(e: ValidationError):
    """
    Normaliza ValidationError a JSON.
    """
    if hasattr(e, "message_dict"):
        return JsonResponse({"detail": e.message_dict}, status=400)
    if hasattr(e, "messages"):
        return JsonResponse({"detail": e.messages}, status=400)
    return _bad_request(str(e), status=400)


def _parse_int(value, field_name: str):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValidationError({field_name: f"{field_name} debe ser entero"})


def _parse_decimal_money(value, field_name: str, allow_null=True) -> Decimal | None:
    """
    Acepta string/number.
    - Si allow_null y viene None/"" -> None
    - Si viene -> Decimal (>=0)
    """
    if value is None or value == "":
        return None if allow_null else DEC_0

    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValidationError({field_name: f"{field_name} inválido (debe ser decimal)"})

    if d < 0:
        raise ValidationError({field_name: f"{field_name} debe ser >= 0"})

    return d.quantize(DEC_0)


# ============================================================
# SUPPLIERS
# ============================================================

@login_required
@require_permission("purchases.supplier.view")
@require_http_methods(["GET"])
def suppliers_list(request):
    qs = Supplier.objects.filter(is_active=True).order_by("name")
    data = [
        {
            "id": s.id,
            "name": s.name,
            "tax_id": s.tax_id,
            "email": s.email,
            "phone": s.phone,
            "address": s.address,
            "is_active": s.is_active,
        }
        for s in qs
    ]
    return JsonResponse({"count": len(data), "results": data})


# ============================================================
# PURCHASE ORDERS
# ============================================================

@login_required
@require_permission("purchases.order.view")
@require_http_methods(["GET"])
def purchase_orders_list(request):
    qs = (
        PurchaseOrder.objects.select_related(
            "supplier",
            "created_by",
            "confirmed_by",
            "received_by",
        )
        .all()
        .order_by("-created_at")[:200]
    )

    data = [
        {
            "id": po.id,
            "supplier_id": po.supplier_id,
            "supplier_name": po.supplier.name,
            "supplier_invoice": po.supplier_invoice,
            "status": po.status,
            "note": po.note,
            "created_by": getattr(po.created_by, "username", None),
            "created_at": po.created_at.isoformat(),
            "confirmed_by": getattr(po.confirmed_by, "username", None),
            "confirmed_at": po.confirmed_at.isoformat() if po.confirmed_at else None,
            "received_by": getattr(po.received_by, "username", None),
            "received_at": po.received_at.isoformat() if po.received_at else None,
        }
        for po in qs
    ]
    return JsonResponse({"count": len(data), "results": data})


@login_required
@require_permission("purchases.order.view")
@require_http_methods(["GET"])
def purchase_order_detail(request, po_id: int):
    try:
        po = (
            PurchaseOrder.objects.select_related(
                "supplier",
                "created_by",
                "confirmed_by",
                "received_by",
            )
            .get(id=po_id)
        )
    except PurchaseOrder.DoesNotExist:
        return _bad_request("PurchaseOrder no existe", status=404)

    lines = po.lines.select_related("product").all().order_by("id")

    data = {
        "id": po.id,
        "supplier_id": po.supplier_id,
        "supplier_name": po.supplier.name,
        "supplier_invoice": po.supplier_invoice,
        "status": po.status,
        "note": po.note,
        "created_by": getattr(po.created_by, "username", None),
        "created_at": po.created_at.isoformat(),
        "confirmed_by": getattr(po.confirmed_by, "username", None),
        "confirmed_at": po.confirmed_at.isoformat() if po.confirmed_at else None,
        "received_by": getattr(po.received_by, "username", None),
        "received_at": po.received_at.isoformat() if po.received_at else None,
        "lines": [
            {
                "id": ln.id,
                "product_id": ln.product_id,
                "product_sku": ln.product.sku,
                "product_name": ln.product.name,
                "quantity": ln.quantity,
                "unit_cost": str(getattr(ln, "unit_cost", None) or DEC_0),
            }
            for ln in lines
        ],
    }
    return JsonResponse({"status": "ok", "purchase_order": data})


@login_required
@require_permission("purchases.order.create")
@require_http_methods(["POST"])
@csrf_exempt
def purchase_order_create(request):
    body = _json_body(request)
    supplier_id = body.get("supplier_id")
    supplier_invoice = body.get("supplier_invoice", "")
    note = body.get("note", "")

    if supplier_id in (None, "", 0):
        return _bad_request("Campo requerido: supplier_id")

    try:
        supplier_id = _parse_int(supplier_id, "supplier_id")
    except ValidationError as e:
        return _validation_error_response(e)

    try:
        supplier = Supplier.objects.get(id=supplier_id, is_active=True)
    except Supplier.DoesNotExist:
        return _bad_request("Supplier no existe o no está activo")

    po = PurchaseOrder.objects.create(
        supplier=supplier,
        supplier_invoice=supplier_invoice,
        note=note,
        created_by=request.user,
    )

    return JsonResponse({"status": "ok", "purchase_order_id": po.id, "po_status": po.status})


@login_required
@require_permission("purchases.order.edit")
@require_http_methods(["POST"])
@csrf_exempt
def purchase_order_add_line(request, po_id: int):
    """
    Agrega una línea a una PO en DRAFT.
    Body: { "product_id": X, "quantity": N, "unit_cost": "123.45"(opcional) }
    Upsert: si ya existe línea del producto, suma quantity.
    - Si unit_cost viene, se guarda/actualiza (último valor).
    """
    body = _json_body(request)
    product_id = body.get("product_id")
    quantity = body.get("quantity")
    unit_cost_raw = body.get("unit_cost", None)

    if product_id in (None, "", 0) or quantity is None:
        return _bad_request("Campos requeridos: product_id, quantity")

    try:
        product_id = _parse_int(product_id, "product_id")
        quantity = _parse_int(quantity, "quantity")
        unit_cost = _parse_decimal_money(unit_cost_raw, "unit_cost", allow_null=True)
    except ValidationError as e:
        return _validation_error_response(e)

    if quantity <= 0:
        return _bad_request("quantity debe ser > 0")

    try:
        po = PurchaseOrder.objects.get(id=po_id)
    except PurchaseOrder.DoesNotExist:
        return _bad_request("PurchaseOrder no existe", status=404)

    if po.status != PurchaseOrder.STATUS_DRAFT:
        return _bad_request("Solo se puede editar una PO en DRAFT")

    try:
        product = Product.objects.get(id=product_id)
    except Product.DoesNotExist:
        return _bad_request("Product no existe")

    if not product.is_active:
        return _bad_request("El producto está inactivo. No se permiten compras/movimientos.")

    line = PurchaseOrderLine.objects.filter(purchase_order=po, product=product).first()
    if line:
        line.quantity = int(line.quantity) + quantity
        if unit_cost is not None:
            line.unit_cost = unit_cost

        try:
            line.full_clean()
            update_fields = ["quantity"]
            if unit_cost is not None:
                update_fields.append("unit_cost")
            line.save(update_fields=update_fields)
        except ValidationError as e:
            return _validation_error_response(e)

        return JsonResponse(
            {
                "status": "ok",
                "mode": "updated",
                "line_id": line.id,
                "purchase_order_id": po.id,
                "quantity": line.quantity,
                "unit_cost": str(getattr(line, "unit_cost", None) or DEC_0),
            }
        )

    create_kwargs = {
        "purchase_order": po,
        "product": product,
        "quantity": quantity,
    }
    if unit_cost is not None:
        create_kwargs["unit_cost"] = unit_cost

    try:
        line = PurchaseOrderLine.objects.create(**create_kwargs)
    except ValidationError as e:
        return _validation_error_response(e)

    return JsonResponse(
        {
            "status": "ok",
            "mode": "created",
            "line_id": line.id,
            "purchase_order_id": po.id,
            "quantity": line.quantity,
            "unit_cost": str(getattr(line, "unit_cost", None) or DEC_0),
        }
    )


@login_required
@require_permission("purchases.order.edit")
@require_http_methods(["POST"])
@csrf_exempt
def purchase_order_update_line(request, po_id: int, line_id: int):
    """
    Actualiza una línea (solo DRAFT).
    Body: { "quantity": N, "unit_cost": "123.45"(opcional) }
    - Si unit_cost no viene, no se modifica.
    """
    body = _json_body(request)
    quantity_raw = body.get("quantity")
    unit_cost_raw = body.get("unit_cost", None)

    if quantity_raw is None and unit_cost_raw is None:
        return _bad_request("Campos requeridos: quantity (y/o unit_cost)")

    try:
        quantity = None
        if quantity_raw is not None:
            quantity = _parse_int(quantity_raw, "quantity")
            if quantity <= 0:
                raise ValidationError({"quantity": "quantity debe ser > 0"})

        unit_cost = _parse_decimal_money(unit_cost_raw, "unit_cost", allow_null=True)
    except ValidationError as e:
        return _validation_error_response(e)

    try:
        po = PurchaseOrder.objects.get(id=po_id)
    except PurchaseOrder.DoesNotExist:
        return _bad_request("PurchaseOrder no existe", status=404)

    if po.status != PurchaseOrder.STATUS_DRAFT:
        return _bad_request("Solo se puede editar una PO en DRAFT")

    try:
        line = PurchaseOrderLine.objects.select_related("product").get(id=line_id, purchase_order=po)
    except PurchaseOrderLine.DoesNotExist:
        return _bad_request("Línea no existe para esta PO", status=404)

    if not line.product.is_active:
        return _bad_request("El producto está inactivo. No se permiten compras/movimientos.")

    update_fields = []
    if quantity is not None:
        line.quantity = quantity
        update_fields.append("quantity")

    if unit_cost is not None:
        line.unit_cost = unit_cost
        update_fields.append("unit_cost")

    try:
        line.full_clean()
        line.save(update_fields=update_fields)
    except ValidationError as e:
        return _validation_error_response(e)

    return JsonResponse(
        {
            "status": "ok",
            "purchase_order_id": po.id,
            "line_id": line.id,
            "quantity": line.quantity,
            "unit_cost": str(getattr(line, "unit_cost", None) or DEC_0),
        }
    )


@login_required
@require_permission("purchases.order.edit")
@require_http_methods(["POST"])
@csrf_exempt
def purchase_order_delete_line(request, po_id: int, line_id: int):
    """
    Elimina una línea (solo DRAFT).
    """
    try:
        po = PurchaseOrder.objects.get(id=po_id)
    except PurchaseOrder.DoesNotExist:
        return _bad_request("PurchaseOrder no existe", status=404)

    if po.status != PurchaseOrder.STATUS_DRAFT:
        return _bad_request("Solo se puede editar una PO en DRAFT")

    try:
        line = PurchaseOrderLine.objects.get(id=line_id, purchase_order=po)
    except PurchaseOrderLine.DoesNotExist:
        return _bad_request("Línea no existe para esta PO", status=404)

    line.delete()
    return JsonResponse({"status": "ok", "purchase_order_id": po.id, "deleted_line_id": line_id})


@login_required
@require_permission("purchases.order.confirm")
@require_http_methods(["POST"])
@csrf_exempt
def purchase_order_confirm(request, po_id: int):
    """
    CONFIRMAR: DRAFT -> CONFIRMED (NO toca stock).
    El stock se impacta en /receive/.
    """
    try:
        po = PurchaseOrder.objects.select_related("supplier").get(id=po_id)
    except PurchaseOrder.DoesNotExist:
        return _bad_request("PurchaseOrder no existe", status=404)

    try:
        po.confirm(user=request.user)
    except ValidationError as e:
        return _validation_error_response(e)

    return JsonResponse({"status": "ok", "purchase_order_id": po.id, "po_status": po.status})


@login_required
@require_permission("purchases.order.receive")
@require_http_methods(["POST"])
@csrf_exempt
def purchase_order_receive(request, po_id: int):
    """
    RECIBIR: CONFIRMED -> RECEIVED (impacta stock con movimientos IN).
    + Hook a Finanzas: PAYABLE (amount real si unit_cost existe).
    """
    try:
        po = PurchaseOrder.objects.select_related("supplier").get(id=po_id)
    except PurchaseOrder.DoesNotExist:
        return _bad_request("PurchaseOrder no existe", status=404)

    try:
        po.receive(user=request.user)
    except ValidationError as e:
        return _validation_error_response(e)

    return JsonResponse({"status": "ok", "purchase_order_id": po.id, "po_status": po.status})


@login_required
@require_permission("purchases.order.cancel")
@require_http_methods(["POST"])
@csrf_exempt
def purchase_order_cancel(request, po_id: int):
    """
    Cancelación (MVP):
    - DRAFT -> CANCELLED
    - CONFIRMED -> CANCELLED (si no fue recibida)
    """
    try:
        po = PurchaseOrder.objects.get(id=po_id)
    except PurchaseOrder.DoesNotExist:
        return _bad_request("PurchaseOrder no existe", status=404)

    try:
        po.cancel(user=request.user)
    except ValidationError as e:
        return _validation_error_response(e)

    return JsonResponse({"status": "ok", "purchase_order_id": po.id, "po_status": po.status})
