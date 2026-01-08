from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum, F, DecimalField, ExpressionWrapper
from django.db.models.functions import Coalesce

from .models import FinancialMovement


DEC_0 = Decimal("0.00")
MONEY_Q = Decimal("0.01")


def _q2(x) -> Decimal:
    """
    Normaliza a 2 decimales (moneda) de forma consistente.
    - Devuelve 0.00 ante valores inválidos.
    - Seguridad MVP: no permite negativos.
    """
    if x is None:
        return DEC_0

    if not isinstance(x, Decimal):
        try:
            x = Decimal(str(x))
        except Exception:
            return DEC_0

    try:
        x = x.quantize(MONEY_Q, rounding=ROUND_HALF_UP)
    except Exception:
        return DEC_0

    if x < DEC_0:
        return DEC_0

    return x


def _has_field(model_cls, field_name: str) -> bool:
    try:
        model_cls._meta.get_field(field_name)
        return True
    except Exception:
        return False


def _safe_rel(obj, rel_name: str):
    """
    Devuelve el related manager si existe (por ej: po.lines / so.sales_lines),
    o None si no está.
    """
    try:
        return getattr(obj, rel_name, None)
    except Exception:
        return None


def _safe_po_total(po) -> Decimal:
    """
    Total PurchaseOrder = sum(quantity * unit_cost) si existe unit_cost.
    Si no existe, devuelve 0.00 (retrocompatible y no rompe).
    """
    lines_rel = _safe_rel(po, "lines")
    if not lines_rel:
        return DEC_0

    line_model = getattr(lines_rel, "model", None)
    if not line_model or not _has_field(line_model, "unit_cost"):
        return DEC_0

    expr = ExpressionWrapper(
        F("quantity") * F("unit_cost"),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    total = lines_rel.aggregate(total=Sum(expr))["total"] or DEC_0
    return _q2(total)


def _safe_so_total(so) -> Decimal:
    """
    Total SalesOrder = sum(quantity * unit_price) si existe unit_price.
    Si no existe, devuelve 0.00 (retrocompatible y no rompe).
    """
    lines_rel = _safe_rel(so, "sales_lines")
    if not lines_rel:
        lines_rel = _safe_rel(so, "lines")

    if not lines_rel:
        return DEC_0

    line_model = getattr(lines_rel, "model", None)
    if not line_model or not _has_field(line_model, "unit_price"):
        return DEC_0

    expr = ExpressionWrapper(
        F("quantity") * F("unit_price"),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    total = lines_rel.aggregate(total=Sum(expr))["total"] or DEC_0
    return _q2(total)


@transaction.atomic
def ensure_payable_for_purchase(po, amount: Decimal | None = None):
    """
    Purchase RECEIVED => PAYABLE (Cuenta a pagar).
    Idempotente: si ya existe (PAYABLE + PURCHASE + source_id), no duplica.

    Regla vendible:
    - Si el movimiento ya está PAID/VOID, NO se recalcula el amount.
      (Movimiento cerrado = inmutable)
    """
    computed = _q2(amount) if amount is not None else _safe_po_total(po)

    obj, created = FinancialMovement.objects.select_for_update().get_or_create(
        movement_type=FinancialMovement.MovementType.PAYABLE,
        source_type=FinancialMovement.SourceType.PURCHASE,
        source_id=po.id,
        defaults={
            "amount": computed,
            "notes": f"Auto: Purchase RECEIVED (PO #{po.id})",
        },
    )

    if not created:
        # Solo ajustamos amount si está OPEN.
        if obj.status == FinancialMovement.Status.OPEN and _q2(obj.amount) != computed:
            obj.amount = computed
            obj.save(update_fields=["amount"])

    return obj


@transaction.atomic
def ensure_receivable_for_sale(so, amount: Decimal | None = None):
    """
    Sale CONFIRMED => RECEIVABLE (Cuenta a cobrar).
    Idempotente: si ya existe (RECEIVABLE + SALE + source_id), no duplica.

    Regla vendible:
    - Si el movimiento ya está PAID/VOID, NO se recalcula el amount.
      (Movimiento cerrado = inmutable)
    """
    computed = _q2(amount) if amount is not None else _safe_so_total(so)

    obj, created = FinancialMovement.objects.select_for_update().get_or_create(
        movement_type=FinancialMovement.MovementType.RECEIVABLE,
        source_type=FinancialMovement.SourceType.SALE,
        source_id=so.id,
        defaults={
            "amount": computed,
            "notes": f"Auto: Sale CONFIRMED (SO #{so.id})",
        },
    )

    if not created:
        if obj.status == FinancialMovement.Status.OPEN and _q2(obj.amount) != computed:
            obj.amount = computed
            obj.save(update_fields=["amount"])

    return obj


# ---------------------------------------------------------
# VOID: void/annul receivable for cancelled sale
# ---------------------------------------------------------

@transaction.atomic
def void_receivable_for_sale(so, reason: str = ""):
    """
    Sale CANCELLED => VOID del RECEIVABLE asociado (si existe).

    Reglas vendibles:
    - Idempotente: si no existe, devuelve None.
    - Si está OPEN -> VOID
    - Si está VOID -> devuelve mv
    - Si está PAID -> ValidationError (MVP: no se “despaga”)
    """
    try:
        mv = FinancialMovement.objects.select_for_update().get(
            movement_type=FinancialMovement.MovementType.RECEIVABLE,
            source_type=FinancialMovement.SourceType.SALE,
            source_id=so.id,
        )
    except FinancialMovement.DoesNotExist:
        return None

    if mv.status == FinancialMovement.Status.PAID:
        raise ValidationError("No se puede anular una venta cuya cuenta a cobrar ya está PAID.")

    # Idempotente
    if mv.status == FinancialMovement.Status.VOID:
        return mv

    # Centralizamos reglas en el modelo (paid_at limpio, idempotencia, etc.)
    mv.void(reason=reason or f"Auto: Sale CANCELLED (SO #{so.id})")
    return mv


# ---------------------------------------------------------
# Summary BI (separado de views, profesional)
# ---------------------------------------------------------

def _sum_amount(qs) -> Decimal:
    """
    Suma robusta de amount (evita loops Python).
    """
    total = qs.aggregate(
        s=Coalesce(Sum("amount"), DEC_0, output_field=DecimalField(max_digits=14, decimal_places=2))
    )["s"]
    return _q2(total)


def build_financial_summary(qs):
    """
    Recibe un queryset ya filtrado y devuelve:
    - buckets (count + amount) para payable/receivable x open/paid (+ void)
    - net_open = receivables_open - payables_open
    """
    def bucket(mtype, st):
        sub = qs.filter(movement_type=mtype, status=st)
        return {
            "count": sub.count(),
            "amount": _q2(_sum_amount(sub)),
        }

    pay_open = bucket(FinancialMovement.MovementType.PAYABLE, FinancialMovement.Status.OPEN)
    pay_paid = bucket(FinancialMovement.MovementType.PAYABLE, FinancialMovement.Status.PAID)
    rec_open = bucket(FinancialMovement.MovementType.RECEIVABLE, FinancialMovement.Status.OPEN)
    rec_paid = bucket(FinancialMovement.MovementType.RECEIVABLE, FinancialMovement.Status.PAID)
    pay_void = bucket(FinancialMovement.MovementType.PAYABLE, FinancialMovement.Status.VOID)
    rec_void = bucket(FinancialMovement.MovementType.RECEIVABLE, FinancialMovement.Status.VOID)

    return {
        "payables": {"open": pay_open, "paid": pay_paid, "void": pay_void},
        "receivables": {"open": rec_open, "paid": rec_paid, "void": rec_void},
        "net_open": _q2(rec_open["amount"] - pay_open["amount"]),
    }
