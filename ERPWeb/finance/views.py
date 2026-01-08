import csv
from datetime import datetime, date
from decimal import Decimal, InvalidOperation

from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db import transaction

from security.decorators import require_permission
from .models import FinancialMovement
from .services import build_financial_summary


# ----------------------------
# Helpers (hardenizados)
# ----------------------------

def _bad_request(msg, status=400, extra=None):
    payload = {"detail": msg}
    if extra is not None:
        payload["extra"] = extra
    return JsonResponse(payload, status=status)


def _ok(payload=None):
    base = {"status": "ok"}
    if payload:
        base.update(payload)
    return JsonResponse(base)


def _to_decimal_str_2(value) -> str:
    """
    Normaliza a string con 2 decimales (BI-friendly).
    """
    if value is None:
        return "0.00"
    if isinstance(value, Decimal):
        try:
            return f"{value.quantize(Decimal('0.01'))}"
        except InvalidOperation:
            return str(value)
    try:
        d = Decimal(str(value))
        return f"{d.quantize(Decimal('0.01'))}"
    except Exception:
        return str(value)


def _safe_int(raw, default=None, min_value=None, max_value=None, field_name="value"):
    if raw is None or raw == "":
        return default
    try:
        v = int(raw)
    except (TypeError, ValueError):
        raise ValidationError(f"{field_name} debe ser un entero")
    if min_value is not None and v < min_value:
        raise ValidationError(f"{field_name} debe ser >= {min_value}")
    if max_value is not None and v > max_value:
        raise ValidationError(f"{field_name} debe ser <= {max_value}")
    return v


def _parse_iso_date_or_datetime(raw: str):
    """
    Acepta:
    - 'YYYY-MM-DD'
    - 'YYYY-MM-DDTHH:MM:SS' (con o sin zona)
    Devuelve datetime aware.
    """
    if raw is None or raw == "":
        return None

    raw = str(raw).strip()

    # 1) Date-only
    try:
        d = date.fromisoformat(raw)
        dt = datetime(d.year, d.month, d.day, 0, 0, 0)
        return timezone.make_aware(dt, timezone.get_current_timezone())
    except Exception:
        pass

    # 2) Datetime ISO
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_current_timezone())
        return dt
    except Exception:
        raise ValidationError("Formato de fecha inválido. Usar YYYY-MM-DD o ISO datetime.")


def _validate_enum(raw, allowed, field_name):
    if raw is None or raw == "":
        return None
    raw = str(raw).strip().upper()
    if raw not in allowed:
        raise ValidationError(f"{field_name} inválido. Allowed: {sorted(list(allowed))}")
    return raw


def _serialize_movement(fm: FinancialMovement):
    return {
        "id": fm.id,
        "movement_type": fm.movement_type,
        "source_type": fm.source_type,
        "source_id": fm.source_id,
        "amount": _to_decimal_str_2(fm.amount),
        "status": fm.status,
        "notes": fm.notes or "",
        "created_at": fm.created_at.isoformat() if fm.created_at else None,
        "paid_at": fm.paid_at.isoformat() if fm.paid_at else None,
    }


def _apply_filters(qs, *, status=None, movement_type=None, source_type=None, dt_from=None, dt_to=None):
    if status:
        qs = qs.filter(status=status)
    if movement_type:
        qs = qs.filter(movement_type=movement_type)
    if source_type:
        qs = qs.filter(source_type=source_type)

    # Rango por created_at (default BI)
    if dt_from:
        qs = qs.filter(created_at__gte=dt_from)
    if dt_to:
        qs = qs.filter(created_at__lte=dt_to)

    return qs


def _get_filter_params(request):
    """
    Retorna filtros validados a partir de querystring.
    """
    allowed_status = {s for s, _ in FinancialMovement.Status.choices}
    allowed_mtype = {s for s, _ in FinancialMovement.MovementType.choices}
    allowed_stype = {s for s, _ in FinancialMovement.SourceType.choices}

    status = _validate_enum(request.GET.get("status"), allowed_status, "status")
    movement_type = _validate_enum(request.GET.get("movement_type"), allowed_mtype, "movement_type")
    source_type = _validate_enum(request.GET.get("source_type"), allowed_stype, "source_type")
    dt_from = _parse_iso_date_or_datetime(request.GET.get("from"))
    dt_to = _parse_iso_date_or_datetime(request.GET.get("to"))

    return status, movement_type, source_type, dt_from, dt_to


# ----------------------------
# Endpoints
# ----------------------------

@login_required
@require_permission("finance.movement.view")
@require_http_methods(["GET"])
def financial_movements_list(request):
    """
    GET /finance/movements/
    - filtros: status, movement_type, source_type, from, to
    - paginado: page, page_size
    - ordering: created_at, -created_at, paid_at, -paid_at, amount, -amount, id, -id
    """
    try:
        status, movement_type, source_type, dt_from, dt_to = _get_filter_params(request)

        ordering = (request.GET.get("ordering") or "-created_at").strip()
        allowed_ordering = {
            "created_at", "-created_at",
            "paid_at", "-paid_at",
            "amount", "-amount",
            "id", "-id",
        }
        if ordering not in allowed_ordering:
            raise ValidationError(f"ordering inválido. Allowed: {sorted(list(allowed_ordering))}")

        page = _safe_int(request.GET.get("page"), default=1, min_value=1, field_name="page")
        page_size = _safe_int(request.GET.get("page_size"), default=50, min_value=1, max_value=500, field_name="page_size")

        qs = FinancialMovement.objects.all()
        qs = _apply_filters(
            qs,
            status=status,
            movement_type=movement_type,
            source_type=source_type,
            dt_from=dt_from,
            dt_to=dt_to,
        ).order_by(ordering)

        total = qs.count()
        offset = (page - 1) * page_size
        items = list(qs[offset: offset + page_size])

        return _ok({
            "count": total,
            "page": page,
            "page_size": page_size,
            "ordering": ordering,
            "results": [_serialize_movement(x) for x in items],
        })

    except ValidationError as e:
        return _bad_request(str(e), status=400)


@login_required
@require_permission("finance.movement.view")
@require_http_methods(["GET"])
def financial_summary(request):
    """
    GET /finance/summary/
    KPI/Agregados BI-friendly.
    Respeta filtros: status, movement_type, source_type, from, to
    """
    try:
        status, movement_type, source_type, dt_from, dt_to = _get_filter_params(request)

        qs = FinancialMovement.objects.all()
        qs = _apply_filters(
            qs,
            status=status,
            movement_type=movement_type,
            source_type=source_type,
            dt_from=dt_from,
            dt_to=dt_to,
        )

        summary = build_financial_summary(qs)

        payload = {
            "as_of": timezone.now().isoformat(),
            "filters": {
                "status": status,
                "movement_type": movement_type,
                "source_type": source_type,
                "from": dt_from.isoformat() if dt_from else None,
                "to": dt_to.isoformat() if dt_to else None,
            },
            "payables": {
                "open": {"count": summary["payables"]["open"]["count"], "amount": _to_decimal_str_2(summary["payables"]["open"]["amount"])},
                "paid": {"count": summary["payables"]["paid"]["count"], "amount": _to_decimal_str_2(summary["payables"]["paid"]["amount"])},
                "void": {"count": summary["payables"]["void"]["count"], "amount": _to_decimal_str_2(summary["payables"]["void"]["amount"])},
            },
            "receivables": {
                "open": {"count": summary["receivables"]["open"]["count"], "amount": _to_decimal_str_2(summary["receivables"]["open"]["amount"])},
                "paid": {"count": summary["receivables"]["paid"]["count"], "amount": _to_decimal_str_2(summary["receivables"]["paid"]["amount"])},
                "void": {"count": summary["receivables"]["void"]["count"], "amount": _to_decimal_str_2(summary["receivables"]["void"]["amount"])},
            },
            "net_open": _to_decimal_str_2(summary["net_open"]),
        }

        return _ok(payload)

    except ValidationError as e:
        return _bad_request(str(e), status=400)


@login_required
@require_permission("finance.movement.view")  # export BI-friendly
@require_http_methods(["GET"])
def financial_export_csv(request):
    """
    GET /finance/export/
    Export CSV BI-friendly (sin paginación).
    Acepta los mismos filtros que /movements/:
      status, movement_type, source_type, from, to
    Ordering whitelisteado.
    """
    try:
        status, movement_type, source_type, dt_from, dt_to = _get_filter_params(request)

        ordering = (request.GET.get("ordering") or "-created_at").strip()
        allowed_ordering = {
            "created_at", "-created_at",
            "paid_at", "-paid_at",
            "amount", "-amount",
            "id", "-id",
        }
        if ordering not in allowed_ordering:
            raise ValidationError(f"ordering inválido. Allowed: {sorted(list(allowed_ordering))}")

        qs = FinancialMovement.objects.all()
        qs = _apply_filters(
            qs,
            status=status,
            movement_type=movement_type,
            source_type=source_type,
            dt_from=dt_from,
            dt_to=dt_to,
        ).order_by(ordering)

        max_rows = 50000
        total = qs.count()
        if total > max_rows:
            return _bad_request(
                f"Export demasiado grande ({total} filas). Ajustar filtros (from/to) para reducir.",
                status=400,
                extra={"max_rows": max_rows, "count": total}
            )

        filename = "finance_movements_export.csv"
        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="{filename}"'

        writer = csv.writer(resp)
        writer.writerow(["id", "movement_type", "source_type", "source_id", "amount", "status", "created_at", "paid_at", "notes"])

        for fm in qs:
            writer.writerow([
                fm.id,
                fm.movement_type,
                fm.source_type,
                fm.source_id,
                _to_decimal_str_2(fm.amount),
                fm.status,
                fm.created_at.isoformat() if fm.created_at else "",
                fm.paid_at.isoformat() if fm.paid_at else "",
                (fm.notes or ""),
            ])

        return resp

    except ValidationError as e:
        return _bad_request(str(e), status=400)


@login_required
@require_permission("finance.movement.pay")
@require_http_methods(["POST"])
@csrf_exempt  # Technical API (JSON) - CSRF intentionally disabled per API_RULES.md
def financial_movement_pay(request, movement_id: int):
    """
    POST /finance/movements/<id>/pay/
    - 404 si no existe
    - 400 si no se puede pagar (VOID, PAID, amount<=0)
    - atomic + select_for_update para evitar doble pago concurrente
    """
    try:
        movement_id = int(movement_id)
        if movement_id <= 0:
            raise ValueError()
    except Exception:
        return _bad_request("movement_id inválido", status=400)

    with transaction.atomic():
        try:
            fm = FinancialMovement.objects.select_for_update().get(id=movement_id)
        except FinancialMovement.DoesNotExist:
            return _bad_request("FinancialMovement no existe", status=404)

        # Auditoría opcional (si el modelo existe en tu proyecto)
        if hasattr(fm, "paid_by_id"):
            fm.paid_by = request.user

        try:
            # Centralizamos reglas en el modelo
            fm.pay()
        except ValidationError as e:
            extra = getattr(e, "message_dict", None) or getattr(e, "messages", None) or str(e)
            return _bad_request(str(e), status=400, extra=extra)

        # Si existe paid_by, lo persistimos (fm.pay() guarda status/paid_at)
        if hasattr(fm, "paid_by_id"):
            fm.save(update_fields=["paid_by"])

    return _ok({
        "movement_id": fm.id,
        "new_status": fm.status,
        "paid_at": fm.paid_at.isoformat() if fm.paid_at else None,
    })
