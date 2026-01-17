from decimal import Decimal, InvalidOperation
from datetime import datetime
from urllib.parse import urlencode
from io import BytesIO

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Case, When, F
from django.db.models.functions import Coalesce
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_POST, require_http_methods
from django.urls import reverse

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

    has_cancel_legacy = (is_super or "purchases.order.cancel" in perm_keys)

    can_cancel_any = (is_super or "purchases.order.cancel_any" in perm_keys)
    can_cancel_own = (is_super or "purchases.order.cancel_own" in perm_keys or has_cancel_legacy)

    return {
        "perm_keys": perm_keys,

        # Sidebar gates
        "can_stock_products": (is_super or "stock.product.view" in perm_keys),
        "can_stock_products_create": (is_super or "stock.product.create" in perm_keys),
        "can_stock_movements": (is_super or "stock.movement.view" in perm_keys),

        "can_purchases": (is_super or "purchases.order.view" in perm_keys),
        "can_sales": (is_super or "sales.order.view" in perm_keys),
        "can_finance": (is_super or "finance.movement.view" in perm_keys),

        # ✅ Proveedores
        "can_purchases_suppliers": (is_super or "purchases.supplier.view" in perm_keys),
        "can_purchases_suppliers_create": (is_super or "purchases.supplier.create" in perm_keys),
        "can_purchases_suppliers_edit": (is_super or "purchases.supplier.edit" in perm_keys),

        # Compras actions (para botones)
        "can_purchases_create": (is_super or "purchases.order.create" in perm_keys),
        "can_purchases_confirm": (is_super or "purchases.order.confirm" in perm_keys),
        "can_purchases_receive": (is_super or "purchases.order.receive" in perm_keys),

        # Cancelación por alcance
        "can_purchases_cancel_any": can_cancel_any,
        "can_purchases_cancel_own": can_cancel_own,
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
    val = _as_decimal(getattr(product, "purchase_cost", None))
    if val is None:
        raise ValueError("El producto no tiene purchase_cost válido.")
    return val


def _po_line_fk_name(PurchaseOrderLine, PurchaseOrder) -> str:
    for f in PurchaseOrderLine._meta.fields:
        rel = getattr(f, "remote_field", None)
        if rel and getattr(rel, "model", None) == PurchaseOrder:
            return f.name
    raise ValueError("No se encontró FK desde PurchaseOrderLine hacia PurchaseOrder.")


def _parse_date_query(q: str):
    if not q:
        return None

    s = q.strip()
    fmts = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"]
    for fmt in fmts:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _po_last_modification_dt(po):
    received_at = getattr(po, "received_at", None)
    if received_at:
        return received_at

    confirmed_at = getattr(po, "confirmed_at", None)
    if confirmed_at:
        return confirmed_at

    status = getattr(po, "status", None) or ""
    if status == "CANCELLED":
        return getattr(po, "updated_at", None)

    return None


def _display_value(v):
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return ", ".join([str(x) for x in v if str(x).strip() != ""])
    if isinstance(v, dict):
        import json
        return json.dumps(v, ensure_ascii=False)
    return str(v).strip()


def _pick_image_url_from_request(request) -> str:
    """
    Robusto: buscamos en varios nombres posibles para no depender del template/form actual.
    Si no viene, devuelve "".
    """
    candidates = [
        "image_url",
        "smart_image_url",
        "lookup_image_url",
        "product_image_url",
        "image_source_url",
    ]
    for k in candidates:
        v = (request.POST.get(k) or "").strip()
        if v:
            return v
    return ""


@login_required
def dashboard(request):
    context = _base_context(request.user)
    return render(request, "ui/dashboard.html", context)


@login_required
def forbidden(request):
    context = _base_context(request.user)
    return render(request, "ui/forbidden.html", context, status=403)


# ============================================================
# ✅ Stock: Productos (listado + alta + detalle + edición)
# ============================================================

@login_required
def stock_products(request):
    context = _base_context(request.user)
    if not _has_perm(request, "stock.product.view"):
        return _forbidden(request, required_permission="stock.product.view")

    q = (request.GET.get("q") or "").strip()

    # ✅ Default: ID DESC (mayor a menor)
    sort = (request.GET.get("sort") or "id").strip()
    direction = (request.GET.get("dir") or "desc").strip().lower()
    if direction not in ("asc", "desc"):
        direction = "desc"

    # ✅ filtro Activo/Inactivo
    raw_active = request.GET.get("active")
    raw_inactive = request.GET.get("inactive")

    # Si no envían ninguno, default: ambos chequeados (mostrar todo)
    if raw_active is None and raw_inactive is None:
        active_checked = True
        inactive_checked = True
    else:
        active_checked = (raw_active == "1")
        inactive_checked = (raw_inactive == "1")

    qs = Product.objects.all()

    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(sku__icontains=q)
            | Q(internal_code__icontains=q)
            | Q(brand__icontains=q)
        )

    # Aplicar filtro de estado operativo
    if active_checked and not inactive_checked:
        qs = qs.filter(is_active=True)
    elif inactive_checked and not active_checked:
        qs = qs.filter(is_active=False)
    elif not active_checked and not inactive_checked:
        qs = qs.none()

    # map de columnas sort permitidas
    sort_map = {
        "id": "id",
        "sku": "sku",
        "name": "name",
        "brand": "brand",
        "stock": "stock",
        "status": "is_active",
        "created": "created_at",
        "updated": "updated_at",
    }
    sort_key = sort_map.get(sort, "id")
    prefix = "" if direction == "asc" else "-"

    # orden final + fallback estable (siempre)
    qs = qs.order_by(f"{prefix}{sort_key}", "-id")

    products = list(qs[:300])

    def _sort_url(col: str) -> str:
        next_dir = "asc"
        if sort == col:
            next_dir = "desc" if direction == "asc" else "asc"

        params = {"q": q, "sort": col, "dir": next_dir}
        if active_checked:
            params["active"] = "1"
        if inactive_checked:
            params["inactive"] = "1"

        return "?" + urlencode({k: v for k, v in params.items() if v not in (None, "")})

    def _arrow(col: str) -> str:
        if sort != col:
            return ""
        return "▲" if direction == "asc" else "▼"

    context.update(
        {
            "products": products,
            "q": q,
            "sort": sort,
            "dir": direction,
            "active_checked": active_checked,
            "inactive_checked": inactive_checked,
            "sort_url": {
                "id": _sort_url("id"),
                "sku": _sort_url("sku"),
                "name": _sort_url("name"),
                "brand": _sort_url("brand"),
                "stock": _sort_url("stock"),
                "status": _sort_url("status"),
                "created": _sort_url("created"),
                "updated": _sort_url("updated"),
            },
            "sort_arrow": {
                "id": _arrow("id"),
                "sku": _arrow("sku"),
                "name": _arrow("name"),
                "brand": _arrow("brand"),
                "stock": _arrow("stock"),
                "status": _arrow("status"),
                "created": _arrow("created"),
                "updated": _arrow("updated"),
            },
        }
    )
    return render(request, "ui/stock_products.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def stock_product_create(request):
    if not _has_perm(request, "stock.product.create"):
        return _forbidden(request, required_permission="stock.product.create")

    context = _base_context(request.user)

    from ui.product_forms import ProductCreateForm

    form = ProductCreateForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            p = None
            try:
                with transaction.atomic():
                    p: Product = form.save(commit=False)
                    # Stock NO se carga manualmente, siempre inicia en 0
                    p.stock = 0
                    p.full_clean()
                    p.save()

                # ✅ Post-save: si viene image_url (Smart Lookup), descargamos y persistimos
                image_url = _pick_image_url_from_request(request)
                if image_url:
                    try:
                        # Firma alineada con stock/models.py (set_image_from_url)
                        saved = p.set_image_from_url(
                            image_url,
                            timeout_seconds=8,
                            max_bytes=5 * 1024 * 1024,
                            force=False,
                        )
                        if saved:
                            # set_image_from_url ya setea image_source_url y el archivo en image
                            p.full_clean()
                            p.save(update_fields=["image", "image_source_url", "updated_at"])
                        else:
                            # Guardamos al menos la fuente si vino URL
                            if hasattr(p, "image_source_url") and not (getattr(p, "image_source_url", "") or "").strip():
                                p.image_source_url = image_url
                            p.save(update_fields=["image_source_url", "updated_at"])
                    except ValidationError as ve:
                        # No bloquea la creación; solo informa
                        if hasattr(ve, "message_dict"):
                            for field, errs in ve.message_dict.items():
                                for e in errs:
                                    messages.warning(request, f"Imagen ({field}): {e}")
                        else:
                            for e in ve.messages:
                                messages.warning(request, f"Imagen: {e}")
                    except Exception as e:
                        messages.warning(request, f"Imagen: no se pudo guardar desde URL ({e})")

                messages.success(request, f"Producto creado: #{p.id} · {p.sku} - {p.name}")
                return redirect("ui:stock_product_detail", pk=p.id)

            except ValidationError as ve:
                if hasattr(ve, "message_dict"):
                    for field, errs in ve.message_dict.items():
                        for e in errs:
                            messages.error(request, f"{field}: {e}")
                else:
                    for e in ve.messages:
                        messages.error(request, e)
            except Exception as e:
                messages.error(request, f"No se pudo crear el producto: {e}")
        else:
            messages.error(request, "Revisá los errores del formulario.")

    context.update({"form": form})
    return render(request, "ui/stock_product_create.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def stock_product_edit(request, pk: int):
    """
    Editar producto (mantiene ID).
    - Bajo ningún concepto toca/elimina/modifica cache Smart Lookup (ProductLookupCache).
    - Permite modificar campos del producto y su imagen.
    - Imagen:
        - remove_image: quita imagen y limpia image_source_url.
        - upload manual: reemplaza imagen (y limpia image_source_url).
        - image_url (Smart Lookup): si no hubo upload manual ni remove_image, reemplaza vía set_image_from_url(force=True).
    """
    # Reusamos permiso existente para no inventar permisos nuevos
    if not _has_perm(request, "stock.product.create"):
        return _forbidden(request, required_permission="stock.product.create")

    p = get_object_or_404(Product, pk=pk)

    from ui.product_forms import ProductEditForm

    form = ProductEditForm(request.POST or None, request.FILES or None, instance=p)

    # Preview robusto para template
    current_image_url = ""
    try:
        if getattr(p, "image", None):
            current_image_url = p.image.url
    except Exception:
        current_image_url = ""

    if request.method == "POST":
        if form.is_valid():
            try:
                remove_image = bool(form.cleaned_data.get("remove_image"))
                has_upload = bool(request.FILES and request.FILES.get("image"))

                with transaction.atomic():
                    prod: Product = form.save(commit=False)

                    # Defensa: NO permitir que edición toque stock
                    prod.stock = getattr(p, "stock", 0)

                    if remove_image:
                        # borrar archivo si existe (fail-safe)
                        try:
                            if getattr(prod, "image", None):
                                prod.image.delete(save=False)
                        except Exception:
                            pass
                        prod.image = None
                        if hasattr(prod, "image_source_url"):
                            prod.image_source_url = ""

                    # Si hubo upload manual, se considera imagen "propia": limpiamos fuente
                    if has_upload and hasattr(prod, "image_source_url"):
                        prod.image_source_url = ""

                    prod.full_clean()
                    prod.save()  # ✅ mantiene PK/ID

                # Post-save: si NO hubo upload manual y NO pidió remove_image, y viene image_url -> reemplazamos por URL
                image_url = _pick_image_url_from_request(request)
                if (not remove_image) and (not has_upload) and image_url:
                    try:
                        saved = prod.set_image_from_url(
                            image_url,
                            timeout_seconds=8,
                            max_bytes=5 * 1024 * 1024,
                            force=True,
                        )
                        if saved:
                            prod.full_clean()
                            prod.save(update_fields=["image", "image_source_url", "updated_at"])
                        else:
                            # al menos guardar la fuente (sin romper)
                            if hasattr(prod, "image_source_url") and not (getattr(prod, "image_source_url", "") or "").strip():
                                prod.image_source_url = image_url
                            prod.save(update_fields=["image_source_url", "updated_at"])
                    except ValidationError as ve:
                        if hasattr(ve, "message_dict"):
                            for field, errs in ve.message_dict.items():
                                for e in errs:
                                    messages.warning(request, f"Imagen ({field}): {e}")
                        else:
                            for e in ve.messages:
                                messages.warning(request, f"Imagen: {e}")
                    except Exception as e:
                        messages.warning(request, f"Imagen: no se pudo guardar desde URL ({e})")

                messages.success(request, f"Producto actualizado: #{prod.id} · {prod.sku} - {prod.name}")
                return redirect("ui:stock_product_detail", pk=prod.id)

            except ValidationError as ve:
                if hasattr(ve, "message_dict"):
                    for field, errs in ve.message_dict.items():
                        for e in errs:
                            messages.error(request, f"{field}: {e}")
                else:
                    for e in ve.messages:
                        messages.error(request, e)
            except Exception as e:
                messages.error(request, f"No se pudo actualizar el producto: {e}")
        else:
            messages.error(request, "Revisá los errores del formulario.")

    context = _base_context(request.user)
    context.update(
        {
            "form": form,
            "p": p,
            "product_image_url": current_image_url,
            "product_image_source_url": (getattr(p, "image_source_url", "") or "").strip(),
        }
    )
    return render(request, "ui/stock_product_edit.html", context)


@login_required
def stock_product_detail(request, pk: int):
    context = _base_context(request.user)
    if not _has_perm(request, "stock.product.view"):
        return _forbidden(request, required_permission="stock.product.view")

    p = get_object_or_404(Product, pk=pk)

    uom_label = dict(Product.UOM_CHOICES).get(
        getattr(p, "unit_of_measure", ""),
        getattr(p, "unit_of_measure", ""),
    )

    tax_label = (
        dict(Product.TAX_CHOICES).get(getattr(p, "tax_type", ""), getattr(p, "tax_type", ""))
        if hasattr(Product, "TAX_CHOICES")
        else getattr(p, "tax_type", "")
    )

    status_label = (
        dict(Product.STATUS_CHOICES).get(getattr(p, "status", ""), getattr(p, "status", ""))
        if hasattr(Product, "STATUS_CHOICES")
        else getattr(p, "status", "")
    )

    stock_value = getattr(p, "stock", 0)

    barcode_value = (getattr(p, "sku", None) or "").strip()

    product_detail_url = request.build_absolute_uri(
        reverse("ui:stock_product_detail", kwargs={"pk": p.id})
    )

    image_url = ""
    try:
        if getattr(p, "image", None):
            image_url = p.image.url
    except Exception:
        image_url = ""

    context.update(
        {
            "p": p,
            "uom_label": uom_label or "-",
            "tax_label": tax_label or "-",
            "status_label": status_label or "-",
            "stock_value": stock_value,
            "purchase_cost_str": _money_str(_as_decimal(getattr(p, "purchase_cost", None)) or Decimal("0.00")),
            "sale_price_str": _money_str(_as_decimal(getattr(p, "sale_price", None)) or Decimal("0.00")),
            "tax_rate_str": _money_str(_as_decimal(getattr(p, "tax_rate", None)) or Decimal("0.00")),
            "barcode_value": barcode_value,
            "product_detail_url": product_detail_url,
            "product_image_url": image_url,
            "product_image_source_url": (getattr(p, "image_source_url", "") or "").strip(),
        }
    )
    return render(request, "ui/stock_product_detail.html", context)


@login_required
def stock_movements(request):
    context = _base_context(request.user)
    if not _has_perm(request, "stock.movement.view"):
        return _forbidden(request, required_permission="stock.movement.view")

    qs = StockMovement.objects.select_related("product").order_by("-created_at")[:200]
    context.update({"movements": qs})
    return render(request, "ui/stock_movements.html", context)


@login_required
def stock_product_movements(request, pk: int):
    context = _base_context(request.user)
    if not _has_perm(request, "stock.movement.view"):
        return _forbidden(request, required_permission="stock.movement.view")

    p = get_object_or_404(Product, pk=pk)

    qs = (
        StockMovement.objects
        .select_related("product")
        .filter(product_id=p.id)
        .order_by("-created_at")[:200]
    )

    context.update({"movements": qs, "product": p})
    return render(request, "ui/stock_movements.html", context)


@login_required
def stock_product_labels(request, pk: int):
    context = _base_context(request.user)
    if not _has_perm(request, "stock.product.view"):
        return _forbidden(request, required_permission="stock.product.view")

    p = get_object_or_404(Product, pk=pk)

    product_detail_url = request.build_absolute_uri(
        reverse("ui:stock_product_detail", kwargs={"pk": p.id})
    )

    barcode_value = (getattr(p, "sku", None) or "").strip()

    context.update(
        {
            "p": p,
            "product_detail_url": product_detail_url,
            "product_url": product_detail_url,
            "barcode_value": barcode_value,
        }
    )
    return render(request, "ui/stock_product_labels.html", context)


@login_required
@require_http_methods(["GET"])
def stock_product_barcode_png(request, pk: int):
    if not _has_perm(request, "stock.product.view"):
        return _forbidden(request, required_permission="stock.product.view")

    p = get_object_or_404(Product, pk=pk)
    value = (getattr(p, "sku", None) or "").strip()
    if not value:
        return HttpResponse(status=404)

    def _is_digits(s: str) -> bool:
        return s.isdigit()

    try:
        from barcode.writer import ImageWriter

        barcode_cls = None
        payload = value

        if _is_digits(value):
            if len(value) == 13:
                barcode_cls = "EAN13"
                payload = value[:12]
            elif len(value) == 12:
                barcode_cls = "EAN13"
                payload = value
            elif len(value) == 8:
                barcode_cls = "EAN8"
                payload = value

        if barcode_cls:
            from barcode import get_barcode_class
            BarcodeClass = get_barcode_class(barcode_cls)
        else:
            from barcode import Code128 as BarcodeClass

        bio = BytesIO()
        code = BarcodeClass(payload, writer=ImageWriter())

        code.write(
            bio,
            options={
                "module_width": 0.25 if barcode_cls in ("EAN13", "EAN8") else 0.20,
                "module_height": 16.0,
                "quiet_zone": 2.0,
                "write_text": True,
                "font_size": 8,
                "text_distance": 4.0,
                "dpi": 300,
            },
        )

        return HttpResponse(bio.getvalue(), content_type="image/png")
    except Exception:
        return HttpResponse(status=500)


@login_required
@require_http_methods(["GET"])
def stock_product_qr_png(request, pk: int):
    if not _has_perm(request, "stock.product.view"):
        return _forbidden(request, required_permission="stock.product.view")

    p = get_object_or_404(Product, pk=pk)

    url = request.build_absolute_uri(
        reverse("ui:stock_product_detail", kwargs={"pk": p.id})
    )

    try:
        import qrcode

        img = qrcode.make(url)
        bio = BytesIO()
        img.save(bio, format="PNG")
        return HttpResponse(bio.getvalue(), content_type="image/png")
    except Exception:
        return HttpResponse(status=500)


# ============================================================
# ✅ UI: Proveedores
# ============================================================

@login_required
def purchases_suppliers(request):
    context = _base_context(request.user)
    if not _has_perm(request, "purchases.supplier.view"):
        return _forbidden(request, required_permission="purchases.supplier.view")

    from purchases.models import Supplier

    q = (request.GET.get("q") or "").strip()

    sort = (request.GET.get("sort") or "id").strip()
    direction = (request.GET.get("dir") or "desc").strip().lower()
    if direction not in ("asc", "desc"):
        direction = "desc"

    qs = Supplier.objects.select_related("created_by").all()

    if q:
        filters = Q()
        if q.isdigit():
            try:
                filters |= Q(id=int(q))
            except Exception:
                pass
        filters |= Q(name__icontains=q)
        filters |= Q(trade_name__icontains=q)
        filters |= Q(tax_id__icontains=q)
        filters |= Q(email__icontains=q)
        filters |= Q(phone__icontains=q)
        filters |= Q(status__icontains=q)
        filters |= Q(created_by__username__icontains=q)
        qs = qs.filter(filters)

    sort_map = {
        "id": "id",
        "name": "name",
        "status": "status",
        "tax_id": "tax_id",
        "created": "created_at",
        "created_by": "created_by__username",
    }
    sort_key = sort_map.get(sort, "id")
    prefix = "" if direction == "asc" else "-"
    qs = qs.order_by(f"{prefix}{sort_key}", "-id")

    suppliers = list(qs[:200])

    def _sort_url(col: str) -> str:
        next_dir = "asc"
        if sort == col:
            next_dir = "desc" if direction == "asc" else "asc"
        params = {"q": q, "sort": col, "dir": next_dir}
        return "?" + urlencode({k: v for k, v in params.items() if v is not None})

    def _arrow(col: str) -> str:
        if sort != col:
            return ""
        return "▲" if direction == "asc" else "▼"

    context.update(
        {
            "suppliers": suppliers,
            "q": q,
            "sort": sort,
            "dir": direction,
            "sort_url": {
                "id": _sort_url("id"),
                "name": _sort_url("name"),
                "status": _sort_url("status"),
                "tax_id": _sort_url("tax_id"),
                "created": _sort_url("created"),
                "created_by": _sort_url("created_by"),
            },
            "sort_arrow": {
                "id": _arrow("id"),
                "name": _arrow("name"),
                "status": _arrow("status"),
                "tax_id": _arrow("tax_id"),
                "created": _arrow("created"),
                "created_by": _arrow("created_by"),
            },
        }
    )
    return render(request, "ui/purchases_suppliers.html", context)


@login_required
def purchases_supplier_detail(request, pk: int):
    context = _base_context(request.user)
    if not _has_perm(request, "purchases.supplier.view"):
        return _forbidden(request, required_permission="purchases.supplier.view")

    from purchases.models import Supplier

    supplier = get_object_or_404(
        Supplier.objects.select_related("created_by").prefetch_related("documents"),
        pk=pk,
    )

    field_labels = {
        "name": "Razón social",
        "trade_name": "Nombre comercial",
        "supplier_type": "Tipo de proveedor",
        "status": "Estado",
        "vat_condition": "Condición IVA",
        "tax_id": "CUIT/Tax ID",
        "document_type": "Tipo de documento",
        "fiscal_address": "Dirección fiscal",
        "province": "Provincia/Estado",
        "postal_code": "Código postal",
        "country": "País",
        "phone": "Teléfono principal",
        "phone_secondary": "Teléfono secundario",
        "email": "Email principal",
        "email_ap": "Email AP",
        "contact_name": "Contacto (nombre)",
        "contact_role": "Contacto (cargo)",
        "fax_or_web": "Fax/Web",
        "payment_terms": "Condiciones de pago",
        "standard_payment_terms": "Plazo de pago estándar",
        "price_list_update_days": "Actualización lista (días)",
        "transaction_currency": "Moneda transacción",
        "account_reference": "Cuenta referencia",
        "classification": "Clasificación/sector",
        "product_category": "Categoría productos",
        "bank_name": "Banco",
        "bank_account_ref": "CBU/IBAN",
        "bank_account_type": "Tipo de cuenta",
        "bank_account_holder": "Titular",
        "bank_account_currency": "Moneda cuenta",
        "tax_condition": "Condición tributaria",
        "retention_category": "Categoría retención",
        "retention_codes": "Códigos retención",
        "internal_notes": "Notas internas",
    }

    def pick_all(fields):
        out = []
        for f in fields:
            raw = getattr(supplier, f, None)
            val = _display_value(raw)
            out.append({"label": field_labels.get(f, f), "value": val if val else "-"})
        return out

    sections = [
        {"title": "Datos generales", "items": pick_all(["tax_id", "vat_condition", "supplier_type", "document_type", "status"])},
        {"title": "Contacto", "items": pick_all(["email", "email_ap", "phone", "phone_secondary", "fax_or_web", "contact_name", "contact_role"])},
        {"title": "Dirección fiscal", "items": pick_all(["fiscal_address", "province", "postal_code", "country"])},
        {"title": "Condiciones comerciales", "items": pick_all([
            "payment_terms", "standard_payment_terms", "price_list_update_days", "transaction_currency",
            "account_reference", "classification", "product_category",
        ])},
        {"title": "Datos bancarios", "items": pick_all(["bank_name", "bank_account_ref", "bank_account_type", "bank_account_holder", "bank_account_currency"])},
        {"title": "Gestión tributaria", "items": pick_all(["tax_condition", "retention_category", "retention_codes"])},
        {"title": "Notas internas", "items": pick_all(["internal_notes"])},
    ]

    extra_fields = getattr(supplier, "extra_fields", None) or {}
    extra_fields_items = []
    if isinstance(extra_fields, dict):
        for k, v in extra_fields.items():
            vv = _display_value(v)
            extra_fields_items.append({"label": str(k), "value": vv if vv else "-"})

    context.update(
        {
            "supplier": supplier,
            "sections": sections,
            "extra_fields_items": extra_fields_items,
            "can_edit_supplier": bool(context.get("can_purchases_suppliers_edit")),
        }
    )
    return render(request, "ui/purchases_supplier_detail.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def purchases_supplier_create(request):
    if not _has_perm(request, "purchases.supplier.create"):
        return _forbidden(request, required_permission="purchases.supplier.create")

    context = _base_context(request.user)

    from purchases.models import Supplier, SupplierDocument
    from ui.forms import SupplierCreateForm

    form = SupplierCreateForm(request.POST or None, request.FILES or None)

    if request.method == "POST":
        if form.is_valid():
            try:
                with transaction.atomic():
                    supplier: Supplier = form.save(commit=False)
                    supplier.created_by = request.user
                    supplier.full_clean()
                    supplier.save()

                    for f in request.FILES.getlist("documents"):
                        SupplierDocument.objects.create(
                            supplier=supplier,
                            file=f,
                            original_name=getattr(f, "name", "") or "",
                            uploaded_by=request.user,
                        )

                messages.success(request, f"Proveedor creado: #{supplier.id} - {supplier.name}")
                return redirect("ui:purchases_supplier_detail", pk=supplier.id)

            except ValidationError as ve:
                if hasattr(ve, "message_dict"):
                    for field, errs in ve.message_dict.items():
                        for e in errs:
                            messages.error(request, f"{field}: {e}")
                else:
                    for e in ve.messages:
                        messages.error(request, e)
            except Exception as e:
                messages.error(request, f"No se pudo crear el proveedor: {e}")
        else:
            messages.error(request, "Revisá los errores del formulario.")

    context.update({"form": form, "mode": "create"})
    return render(request, "ui/purchases_supplier_create.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def purchases_supplier_edit(request, pk: int):
    if not _has_perm(request, "purchases.supplier.edit"):
        return _forbidden(request, required_permission="purchases.supplier.edit")

    context = _base_context(request.user)

    from purchases.models import Supplier, SupplierDocument
    from ui.forms import SupplierCreateForm

    supplier = get_object_or_404(Supplier, pk=pk)

    import json
    initial = {}
    if isinstance(getattr(supplier, "extra_fields", None), dict) and supplier.extra_fields:
        initial["extra_fields_text"] = json.dumps(supplier.extra_fields, ensure_ascii=False)

    form = SupplierCreateForm(request.POST or None, request.FILES or None, instance=supplier, initial=initial)

    if request.method == "POST":
        if form.is_valid():
            try:
                with transaction.atomic():
                    sup = form.save(commit=False)
                    sup.full_clean()
                    sup.save()

                    for f in request.FILES.getlist("documents"):
                        SupplierDocument.objects.create(
                            supplier=sup,
                            file=f,
                            original_name=getattr(f, "name", "") or "",
                            uploaded_by=request.user,
                        )

                messages.success(request, f"Proveedor actualizado: #{supplier.id} - {supplier.name}")
                return redirect("ui:purchases_supplier_detail", pk=supplier.id)

            except ValidationError as ve:
                if hasattr(ve, "message_dict"):
                    for field, errs in ve.message_dict.items():
                        for e in errs:
                            messages.error(request, f"{field}: {e}")
                else:
                    for e in ve.messages:
                        messages.error(request, e)
            except Exception as e:
                messages.error(request, f"No se pudo actualizar el proveedor: {e}")
        else:
            messages.error(request, "Revisá los errores del formulario.")

    context.update({"form": form, "supplier": supplier, "mode": "edit"})
    return render(request, "ui/purchases_supplier_edit.html", context)


# ============================================================
# Compras: Órdenes (tu código intacto)
# ============================================================

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

    sort = (request.GET.get("sort") or "id").strip()
    direction = (request.GET.get("dir") or "desc").strip().lower()
    if direction not in ("asc", "desc"):
        direction = "desc"

    qs = (
        PurchaseOrder.objects
        .select_related("supplier", "created_by")
        .annotate(
            last_modified_dt=Coalesce(
                F("received_at"),
                F("confirmed_at"),
                Case(
                    When(status="CANCELLED", then=F("updated_at")),
                    default=None,
                ),
            )
        )
        .all()
    )

    if q:
        filters = Q()

        if q.isdigit():
            try:
                filters |= Q(id=int(q))
            except Exception:
                pass

        q_upper = q.strip().upper()
        if q_upper in {"DRAFT", "CONFIRMED", "RECEIVED", "CANCELLED"}:
            filters |= Q(status=q_upper)
        else:
            filters |= Q(status__icontains=q)

        filters |= Q(supplier__name__icontains=q)
        filters |= Q(created_by__username__icontains=q)

        q_date = _parse_date_query(q)
        if q_date:
            filters |= Q(created_at__date=q_date)

        qs = qs.filter(filters)

    sort_map = {
        "id": "id",
        "supplier": "supplier__name",
        "status": "status",
        "created": "created_at",
        "created_by": "created_by__username",
        "lastmod": "last_modified_dt",
    }

    sort_key = sort_map.get(sort, "id")
    prefix = "" if direction == "asc" else "-"
    qs = qs.order_by(f"{prefix}{sort_key}", "-id")

    orders = list(qs[:200])

    rows = []
    for po in orders:
        rows.append(
            {
                "po": po,
                "created_by_display": (getattr(getattr(po, "created_by", None), "username", None) or "-"),
                "last_modified_at": _po_last_modification_dt(po),
            }
        )

    def _sort_url(col: str) -> str:
        next_dir = "asc"
        if sort == col:
            next_dir = "desc" if direction == "asc" else "asc"
        params = {"q": q, "sort": col, "dir": next_dir}
        return "?" + urlencode({k: v for k, v in params.items() if v is not None})

    def _arrow(col: str) -> str:
        if sort != col:
            return ""
        return "▲" if direction == "asc" else "▼"

    context.update(
        {
            "rows": rows,
            "q": q,
            "sort": sort,
            "dir": direction,
            "sort_url": {
                "id": _sort_url("id"),
                "supplier": _sort_url("supplier"),
                "status": _sort_url("status"),
                "created": _sort_url("created"),
                "created_by": _sort_url("created_by"),
                "lastmod": _sort_url("lastmod"),
            },
            "sort_arrow": {
                "id": _arrow("id"),
                "supplier": _arrow("supplier"),
                "status": _arrow("status"),
                "created": _arrow("created"),
                "created_by": _arrow("created_by"),
                "lastmod": _arrow("lastmod"),
            },
        }
    )
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

    status = getattr(po, "status", "") or ""
    cancelable_status = (status not in ("RECEIVED", "CANCELLED"))

    can_cancel_po = False
    if cancelable_status:
        if context.get("can_purchases_cancel_any"):
            can_cancel_po = True
        elif context.get("can_purchases_cancel_own"):
            can_cancel_po = (getattr(po, "created_by_id", None) == getattr(request.user, "id", None))

    context.update(
        {
            "po": po,
            "lines": lines,
            "line_items": line_items,
            "po_total": po_total,
            "po_total_str": _money_str(po_total),
            "can_cancel_po": can_cancel_po,
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
    context = _base_context(request.user)

    if not (context.get("can_purchases_cancel_any") or context.get("can_purchases_cancel_own")):
        return _forbidden(request, required_permission="purchases.order.cancel_own")

    from purchases.models import PurchaseOrder

    with transaction.atomic():
        po = get_object_or_404(PurchaseOrder.objects.select_for_update(), pk=pk)

        if not context.get("can_purchases_cancel_any"):
            if getattr(po, "created_by_id", None) != getattr(request.user, "id", None):
                return _forbidden(request, required_permission="purchases.order.cancel_own")

        try:
            po.cancel(request.user)
            messages.success(request, f"PO#{po.id} cancelada correctamente.")
        except Exception as e:
            messages.error(request, f"No se pudo cancelar PO#{pk}: {e}")

    return redirect("ui:purchases_order_detail", pk=pk)


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
                prepared_lines = []
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

                    prepared_lines.append(
                        {
                            "product": product,
                            "quantity": qty,
                            "unit_cost": unit_cost,
                        }
                    )

                if not prepared_lines:
                    raise ValueError("Cargá al menos 1 línea válida.")

                with transaction.atomic():
                    po = PurchaseOrder.objects.create(
                        supplier_id=supplier_id,
                        note=note,
                        created_by=request.user,
                    )

                    fk_name = _po_line_fk_name(PurchaseOrderLine, PurchaseOrder)

                    for ln in prepared_lines:
                        kwargs = {
                            fk_name: po,
                            "product": ln["product"],
                            "quantity": ln["quantity"],
                            "unit_cost": ln["unit_cost"],
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
