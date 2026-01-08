import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError

from security.decorators import require_permission
from .models import SalesOrder, SalesOrderLine
from stock.models import Product


DEC_0 = Decimal("0.00")
MONEY_Q = Decimal("0.01")


def _json_body(request):
    """
    JSON robusto (vacío o inválido -> {}).
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
    Normaliza ValidationError a un JSON consistente.
    """
    if hasattr(e, "message_dict"):
        return JsonResponse({"detail": e.message_dict}, status=400)
    if hasattr(e, "messages"):
        return JsonResponse({"detail": e.messages}, status=400)
    return JsonResponse({"detail": str(e)}, status=400)


def _parse_int(value, field_name: str):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValidationError({field_name: f"{field_name} debe ser entero"})


def _parse_decimal_money(value, field_name: str, allow_null=True) -> Decimal | None:
    """
    Parseo robusto de dinero:
    - None / "" -> None si allow_null, si no -> 0.00
    - >= 0
    - quantize a 0.01
    """
    if value is None or value == "":
        return None if allow_null else DEC_0

    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValidationError({field_name: f"{field_name} inválido (debe ser decimal)"})

    if d < 0:
        raise ValidationError({field_name: f"{field_name} debe ser >= 0"})

    return d.quantize(MONEY_Q, rounding=ROUND_HALF_UP)


@login_required
@require_permission("sales.order.view")
@require_http_methods(["GET"])
def sales_orders_list(request):
    qs = (
        SalesOrder.objects.select_related("created_by", "confirmed_by")
        .all()
        .order_by("-created_at")[:200]
    )
    data = [
        {
            "id": so.id,
            "customer_name": so.customer_name,
            "customer_doc": so.customer_doc,
            "status": so.status,
            "note": so.note,
            "created_by": getattr(so.created_by, "username", None),
            "created_at": so.created_at.isoformat(),
            "confirmed_by": getattr(so.confirmed_by, "username", None),
            "confirmed_at": so.confirmed_at.isoformat() if so.confirmed_at else None,
            # si existen campos de cancelación (PASO 4), los mostramos sin romper
            "cancelled_by": getattr(getattr(so, "cancelled_by", None), "username", None),
            "cancelled_at": so.cancelled_at.isoformat() if getattr(so, "cancelled_at", None) else None,
            "cancel_reason": getattr(so, "cancel_reason", "") if hasattr(so, "cancel_reason") else "",
        }
        for so in qs
    ]
    return JsonResponse({"count": len(data), "results": data})


@login_required
@require_permission("sales.order.view")
@require_http_methods(["GET"])
def sales_order_detail(request, so_id: int):
    try:
        so = SalesOrder.objects.select_related("created_by", "confirmed_by").get(id=so_id)
    except SalesOrder.DoesNotExist:
        return _bad_request("SalesOrder no existe", status=404)

    lines = so.lines.select_related("product").all().order_by("id")

    data = {
        "id": so.id,
        "customer_name": so.customer_name,
        "customer_doc": so.customer_doc,
        "status": so.status,
        "note": so.note,
        "created_at": so.created_at.isoformat(),
        "confirmed_at": so.confirmed_at.isoformat() if so.confirmed_at else None,
        "cancelled_at": so.cancelled_at.isoformat() if getattr(so, "cancelled_at", None) else None,
        "cancel_reason": getattr(so, "cancel_reason", "") if hasattr(so, "cancel_reason") else "",
        "lines": [
            {
                "id": ln.id,
                "product_id": ln.product_id,
                "product_sku": ln.product.sku,
                "product_name": ln.product.name,
                "quantity": ln.quantity,
                "unit_price": str(getattr(ln, "unit_price", None) or DEC_0),
            }
            for ln in lines
        ],
    }
    return JsonResponse({"status": "ok", "sales_order": data})


@login_required
@require_permission("sales.order.create")
@require_http_methods(["POST"])
@csrf_exempt
def sales_order_create(request):
    body = _json_body(request)
    customer_name = (body.get("customer_name") or "").strip()
    customer_doc = (body.get("customer_doc") or "").strip()
    note = body.get("note", "")
    if note is None:
        note = ""
    if not isinstance(note, str):
        note = str(note)

    if not customer_name:
        return _bad_request("Campo requerido: customer_name")

    so = SalesOrder.objects.create(
        customer_name=customer_name,
        customer_doc=customer_doc,
        note=note,
        created_by=request.user,
    )
    return JsonResponse({"status": "ok", "sales_order_id": so.id, "so_status": so.status})


@login_required
@require_permission("sales.order.edit")
@require_http_methods(["POST"])
@csrf_exempt
def sales_order_add_line(request, so_id: int):
    """
    Upsert por (sales_order, product):
    - si existe la línea → suma quantity
    - si no existe → crea
    Body: { "product_id": X, "quantity": N, "unit_price": "123.45"(opcional) }

    Si unit_price viene, se guarda/actualiza (último valor).
    """
    body = _json_body(request)
    product_id = body.get("product_id")
    quantity_raw = body.get("quantity")
    unit_price_raw = body.get("unit_price", None)

    if product_id in (None, "", 0) or quantity_raw is None:
        return _bad_request("Campos requeridos: product_id, quantity")

    try:
        product_id = _parse_int(product_id, "product_id")
        quantity = _parse_int(quantity_raw, "quantity")
        unit_price = _parse_decimal_money(unit_price_raw, "unit_price", allow_null=True)
    except ValidationError as e:
        return _validation_error_response(e)

    if quantity <= 0:
        return _bad_request("quantity debe ser > 0")

    try:
        so = SalesOrder.objects.get(id=so_id)
    except SalesOrder.DoesNotExist:
        return _bad_request("SalesOrder no existe", status=404)

    if so.status != SalesOrder.STATUS_DRAFT:
        return _bad_request("Solo se puede editar una SO en DRAFT")

    try:
        product = Product.objects.get(id=product_id)
    except Product.DoesNotExist:
        return _bad_request("Product no existe")

    if not product.is_active:
        return _bad_request("El producto está inactivo. No se permiten movimientos/ventas.")

    line = SalesOrderLine.objects.filter(sales_order=so, product=product).first()
    if line:
        line.quantity = int(line.quantity) + quantity
        if unit_price is not None:
            line.unit_price = unit_price

        try:
            line.full_clean()
            update_fields = ["quantity"]
            if unit_price is not None:
                update_fields.append("unit_price")
            line.save(update_fields=update_fields)
        except ValidationError as e:
            return _validation_error_response(e)

        return JsonResponse(
            {
                "status": "ok",
                "mode": "updated",
                "line_id": line.id,
                "sales_order_id": so.id,
                "quantity": line.quantity,
                "unit_price": str(getattr(line, "unit_price", None) or DEC_0),
            }
        )

    line = SalesOrderLine(
        sales_order=so,
        product=product,
        quantity=quantity,
    )
    if unit_price is not None:
        line.unit_price = unit_price

    try:
        line.full_clean()
        line.save()
    except ValidationError as e:
        return _validation_error_response(e)

    return JsonResponse(
        {
            "status": "ok",
            "mode": "created",
            "line_id": line.id,
            "sales_order_id": so.id,
            "quantity": line.quantity,
            "unit_price": str(getattr(line, "unit_price", None) or DEC_0),
        }
    )


@login_required
@require_permission("sales.order.edit")
@require_http_methods(["POST"])
@csrf_exempt
def sales_order_update_line(request, so_id: int, line_id: int):
    """
    Actualiza una línea (solo DRAFT).
    Body: { "quantity": N, "unit_price": "123.45"(opcional) }
    - Si unit_price no viene (o viene vacío), no se modifica.
    """
    body = _json_body(request)
    quantity_raw = body.get("quantity")
    unit_price_raw = body.get("unit_price", None)

    if quantity_raw is None and unit_price_raw is None:
        return _bad_request("Campos requeridos: quantity (y/o unit_price)")

    try:
        quantity = None
        if quantity_raw is not None:
            quantity = _parse_int(quantity_raw, "quantity")
            if quantity <= 0:
                raise ValidationError({"quantity": "quantity debe ser > 0"})

        unit_price = _parse_decimal_money(unit_price_raw, "unit_price", allow_null=True)
    except ValidationError as e:
        return _validation_error_response(e)

    try:
        so = SalesOrder.objects.get(id=so_id)
    except SalesOrder.DoesNotExist:
        return _bad_request("SalesOrder no existe", status=404)

    if so.status != SalesOrder.STATUS_DRAFT:
        return _bad_request("Solo se puede editar una SO en DRAFT")

    try:
        line = SalesOrderLine.objects.select_related("product").get(id=line_id, sales_order=so)
    except SalesOrderLine.DoesNotExist:
        return _bad_request("Línea no existe", status=404)

    if not line.product.is_active:
        return _bad_request("El producto está inactivo. No se permiten movimientos/ventas.")

    update_fields = []
    if quantity is not None:
        line.quantity = quantity
        update_fields.append("quantity")

    if unit_price is not None:
        line.unit_price = unit_price
        update_fields.append("unit_price")

    if not update_fields:
        return _bad_request("No hay cambios para aplicar")

    try:
        line.full_clean()
        line.save(update_fields=update_fields)
    except ValidationError as e:
        return _validation_error_response(e)

    return JsonResponse(
        {
            "status": "ok",
            "sales_order_id": so.id,
            "line_id": line.id,
            "quantity": line.quantity,
            "unit_price": str(getattr(line, "unit_price", None) or DEC_0),
        }
    )


@login_required
@require_permission("sales.order.edit")
@require_http_methods(["POST"])
@csrf_exempt
def sales_order_delete_line(request, so_id: int, line_id: int):
    try:
        so = SalesOrder.objects.get(id=so_id)
    except SalesOrder.DoesNotExist:
        return _bad_request("SalesOrder no existe", status=404)

    if so.status != SalesOrder.STATUS_DRAFT:
        return _bad_request("Solo se puede editar una SO en DRAFT")

    try:
        line = SalesOrderLine.objects.get(id=line_id, sales_order=so)
    except SalesOrderLine.DoesNotExist:
        return _bad_request("Línea no existe", status=404)

    deleted_id = line.id
    line.delete()
    return JsonResponse({"status": "ok", "sales_order_id": so.id, "deleted_line_id": deleted_id})


@login_required
@require_permission("sales.order.confirm")
@require_http_methods(["POST"])
@csrf_exempt
def sales_order_confirm(request, so_id: int):
    try:
        so = SalesOrder.objects.get(id=so_id)
    except SalesOrder.DoesNotExist:
        return _bad_request("SalesOrder no existe", status=404)

    try:
        so.confirm(user=request.user)
    except ValidationError as e:
        return _validation_error_response(e)

    return JsonResponse({"status": "ok", "sales_order_id": so.id, "so_status": so.status})


@login_required
@require_permission("sales.order.cancel")
@require_http_methods(["POST"])
@csrf_exempt
def sales_order_cancel(request, so_id: int):
    """
    Cancelación (PASO 4):
    - soporta body opcional: {"reason": "..."}
    - llama cancel(user, reason) si el modelo lo soporta
    - si el modelo aún usa cancel() sin args, cae retrocompatible.
    """
    try:
        so = SalesOrder.objects.get(id=so_id)
    except SalesOrder.DoesNotExist:
        return _bad_request("SalesOrder no existe", status=404)

    body = _json_body(request)
    reason = body.get("reason", "")
    if reason is None:
        reason = ""
    if not isinstance(reason, str):
        reason = str(reason)
    reason = reason.strip()

    try:
        try:
            # Nuevo modelo (vendible): cancel(user, reason)
            so.cancel(user=request.user, reason=reason)
        except TypeError:
            # Modelo viejo: cancel()
            so.cancel()
    except ValidationError as e:
        return _validation_error_response(e)

    return JsonResponse(
        {
            "status": "ok",
            "sales_order_id": so.id,
            "so_status": so.status,
            "cancel_reason": reason,
        }
    )

