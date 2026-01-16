# ERPWeb/stock/views.py
import json
import os
import re
from typing import Any, Dict, Optional, Tuple, List
from urllib.parse import urlparse

import httpx

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.core.exceptions import ValidationError
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.conf import settings

from security.decorators import require_permission
from .models import Product, StockMovement


def _json_body(request):
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return {}


# ============================================================
# ✅ Smart Lookup (v3.3) - Google-first (SerpAPI) + fallbacks + Heuristic extractor (NO IA)
# - No escribe en DB
# - Devuelve sugerencias normalizadas para autocompletar alta de producto
# - Cache usando Django cache (configurable en settings.py)
#   - TTL positivo: 7 días
#   - TTL negativo: 12 hs
# - Providers (orden):
#   1) SerpAPI (Google engine)  ✅ PRIORITARIO
#   2) Heuristic extractor (desde evidencia SerpAPI; determinístico)
#   3) OpenFoodFacts (gratis) ✅ PUEDE CORREGIR heurística (precedencia)
#   4) UPCItemDB (trial)
# - force=true para saltar cache en pruebas
# - debug_trace solo si DEBUG=True
# ============================================================

SMART_LOOKUP_TTL_SECONDS = 60 * 60 * 24 * 7   # 7 días (FOUND)
SMART_LOOKUP_NEG_TTL_SECONDS = 60 * 60 * 12   # 12 hs (NOT_FOUND)

_OFF_URL = "https://world.openfoodfacts.org/api/v0/product/{barcode}.json"

# UPCItemDB (trial)
_UPCITEMDB_TRIAL_URL = "https://api.upcitemdb.com/prod/trial/lookup"

# SerpAPI
_SERPAPI_URL = "https://serpapi.com/search.json"


def _cache_key(barcode: str) -> str:
    return f"smart_lookup:product:{barcode}"


def _is_probable_barcode(s: str) -> bool:
    """
    Validación blanda:
    - Longitud razonable 6..32 (EAN/SKU internos)
    """
    if not s:
        return False
    if len(s) < 6 or len(s) > 32:
        return False
    return True


def _norm_string(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _smart_response(
    barcode: str,
    data: Dict[str, Any],
    suggested_fields: list[str],
    missing_fields: list[str],
    sources: list[Dict[str, Any]],
    cached: bool,
    warnings: Optional[list[str]] = None,
    evidence: Optional[Dict[str, Any]] = None,
    debug_trace: Optional[list[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    payload = {
        "barcode": barcode,
        "data": data,
        "suggested_fields": suggested_fields,
        "missing_fields": missing_fields,
        "sources": sources,
        "cached": cached,
        "warnings": warnings or [],
    }
    if evidence:
        payload["evidence"] = evidence

    # Solo devolvemos trace si DEBUG=True (para no exponer internals en prod)
    if getattr(settings, "DEBUG", False) and debug_trace:
        payload["debug_trace"] = debug_trace

    return payload


def _compute_suggested_and_missing(data: Dict[str, Any]) -> Tuple[list[str], list[str]]:
    fields = [
        "codigo_barra",
        "nombre",
        "marca",
        "categoria",
        "descripcion",
        "peso_volumen",
        "imagen_url",
    ]
    suggested: List[str] = []
    missing: List[str] = []
    for f in fields:
        v = data.get(f)
        if v is None or (isinstance(v, str) and not v.strip()):
            missing.append(f)
        else:
            suggested.append(f)
    return suggested, missing


def _merge_best(base: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge conservador:
    - Si base no tiene un campo, lo toma de candidate.
    - Nunca pisa un campo ya completo con None/vacío.
    """
    out = dict(base)
    for k, v in candidate.items():
        if k not in out:
            out[k] = v
            continue
        if out.get(k) in (None, "", "-") and v not in (None, "", "-"):
            out[k] = v
    return out


def _apply_source_precedence(
    best: Dict[str, Any],
    better_source: Optional[Dict[str, Any]],
    *,
    source_name: str,
    fields: Tuple[str, ...],
    trace: list,
) -> Dict[str, Any]:
    """
    ✅ PASO 1: Precedencia controlada.
    Permite que una fuente de mayor calidad (OpenFoodFacts) CORRIJA ciertos campos,
    aunque el heurístico ya los haya completado.

    - Solo pisa campos listados en `fields`.
    - Solo pisa si `better_source` trae valor no vacío.
    - No cambia shape de respuesta.
    - Deja rastro en debug_trace (si DEBUG=True).
    """
    if not better_source:
        return best

    out = dict(best)
    changed = []

    for f in fields:
        bv = _norm_string(better_source.get(f))
        if not bv:
            continue

        cur = _norm_string(out.get(f))
        if cur == bv:
            continue

        # Pisamos solo si la fuente "mejor" tiene valor y es distinto
        out[f] = bv
        changed.append(f)

    if changed:
        trace.append(
            {
                "provider": "precedence",
                "ok": True,
                "found": True,
                "note": f"{source_name}_overrides={','.join(changed)}",
            }
        )

    return out


# ============================================================
# ✅ Heuristic Extractor (sin IA)
# ============================================================

_WEIGHT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kg|kgr|g|gr|mg|ml|l|lt|cc)\b", re.IGNORECASE)
_PACK_RE = re.compile(r"\b(x|\*)\s?(\d{1,3})\b", re.IGNORECASE)

# Keywords simples -> categorías genéricas (podés expandir esto con el tiempo)
_CATEGORY_RULES = [
    # Cuidado personal
    (re.compile(r"\b(shampoo|acondicionador|cabello|capilar)\b", re.I), "Cuidado personal · Cabello"),
    (re.compile(r"\b(jab[oó]n|gel de ducha|ducha)\b", re.I), "Cuidado personal · Higiene"),
    (re.compile(r"\b(desodorante|antitranspirante|talco|talquera|pies)\b", re.I), "Cuidado personal · Higiene"),
    (re.compile(r"\b(crema|loc[ií]on|hidratante)\b", re.I), "Cuidado personal · Piel"),
    (re.compile(r"\b(afeitar|shaving|after shave)\b", re.I), "Cuidado personal · Afeitado"),

    # Alimentos / bebidas
    (re.compile(r"\b(yerba|mate)\b", re.I), "Alimentos · Infusiones"),
    (re.compile(r"\b(t[eé]|te en hebras|infusi[oó]n)\b", re.I), "Alimentos · Infusiones"),
    (re.compile(r"\b(galletitas|galletas|snack)\b", re.I), "Alimentos · Snacks"),
    (re.compile(r"\b(arroz|fideos|pastas)\b", re.I), "Alimentos · Secos"),
    (re.compile(r"\b(leche|yogur|queso)\b", re.I), "Alimentos · Lácteos"),

    # Limpieza
    (re.compile(r"\b(lavandina|cloro|desinfectante)\b", re.I), "Hogar · Limpieza"),
    (re.compile(r"\b(detergente|lavavajillas)\b", re.I), "Hogar · Limpieza"),
    (re.compile(r"\b(lavandina|limpiador|multiuso)\b", re.I), "Hogar · Limpieza"),
]

# Lista corta de dominios confiables (podés tunearla)
_TRUSTED_DOMAIN_HINTS = [
    "carrefour", "coto", "disco", "jumbo", "vea",
    "farmacity", "simply", "dia", "changomas",
    "mercadolibre", "mlstatic",
    "garbarino", "musimundo",
]

# Algunas palabras que suelen “ensuciar” títulos
_TITLE_SPLIT_TOKENS = ["|", " - ", " – ", " — ", " · "]


def _host_from_url(url: Optional[str]) -> str:
    if not url:
        return ""
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def _clean_title(title: Optional[str]) -> Optional[str]:
    t = _norm_string(title)
    if not t:
        return None
    # corta en tokens tipo " - Sitio"
    for tok in _TITLE_SPLIT_TOKENS:
        if tok in t:
            parts = [p.strip() for p in t.split(tok) if p.strip()]
            if parts:
                # Nos quedamos con el primer segmento, suele ser el producto
                t = parts[0]
            break
    # colapsa espacios
    t = re.sub(r"\s+", " ", t).strip()
    return t or None


def _extract_weight(text: str) -> Optional[str]:
    if not text:
        return None
    m = _WEIGHT_RE.search(text)
    if not m:
        return None
    num = m.group(1).replace(",", ".")
    unit = m.group(2).lower()
    # normaliza unidades
    unit_map = {"kgr": "kg", "gr": "g", "lt": "l"}
    unit = unit_map.get(unit, unit)

    # intenta capturar pack "x N"
    pack = None
    pm = _PACK_RE.search(text)
    if pm:
        pack = pm.group(2)

    if pack:
        return f"{pack}x {num} {unit}"
    return f"{num} {unit}"


def _extract_brand(text: str) -> Optional[str]:
    """
    Heurística blanda:
    - Busca patrones tipo "marca X" / "de X" (muy conservador).
    - También detecta "Algabo" como ejemplo frecuente del caso real (podés sumar marcas luego).
    """
    if not text:
        return None

    # Patrón "marca: X" o "marca X"
    m = re.search(r"\bmarca[:\s]+([A-Za-z0-9ÁÉÍÓÚÑáéíóúñ'\-\.]{2,30})\b", text, flags=re.I)
    if m:
        return _norm_string(m.group(1))

    # “Algabo” aparece mucho en cuidado personal (tu ejemplo)
    if re.search(r"\balgabo\b", text, flags=re.I):
        return "Algabo"

    return None


def _infer_category(text: str) -> Optional[str]:
    if not text:
        return None
    for rx, cat in _CATEGORY_RULES:
        if rx.search(text):
            return cat
    return None


def _score_result(item: Dict[str, Any], barcode: str) -> int:
    title = (item.get("title") or "") or ""
    snippet = (item.get("snippet") or "") or ""
    link = (item.get("link") or "") or ""
    host = _host_from_url(link)

    haystack = f"{title} {snippet} {link}".lower()

    score = 0
    if barcode and barcode in haystack:
        score += 3

    # dominio “confiable” suma (sin pasarnos)
    for d in _TRUSTED_DOMAIN_HINTS:
        if d in host:
            score += 1
            break

    # resultados vacíos penalizan
    if not title:
        score -= 1
    if not snippet:
        score -= 1

    # penaliza PDFs / noticias (ruido)
    if ".pdf" in link.lower():
        score -= 2

    return score


def _heuristic_extract_from_evidence(barcode: str, evidence: Dict[str, Any], trace: list) -> Optional[Dict[str, Any]]:
    top = evidence.get("top_results") or []
    if not top:
        trace.append({"provider": "heuristic_extractor", "ok": True, "found": False, "note": "no_evidence"})
        return None

    scored = []
    for it in top:
        scored.append((_score_result(it, barcode), it))
    scored.sort(key=lambda x: x[0], reverse=True)

    best_item = scored[0][1]
    best_score = scored[0][0]

    # combinamos textos para extraer marca/cat/peso
    title_raw = best_item.get("title")
    snippet_raw = best_item.get("snippet")
    image_url = best_item.get("image") or best_item.get("thumbnail")

    # ✅ MEJORA 1: si el mejor item no trae imagen, buscamos en los otros top results
    if not _norm_string(image_url):
        for it in top:
            cand = it.get("thumbnail") or it.get("image")
            if _norm_string(cand):
                image_url = cand
                break

    title = _clean_title(title_raw) or _norm_string(title_raw)
    snippet = _norm_string(snippet_raw)

    combo = " ".join([t for t in [title or "", snippet or ""] if t]).strip()

    peso = _extract_weight(combo)
    marca = _extract_brand(combo)
    categoria = _infer_category(combo)

    # nivel de confianza simple:
    # - base 0.55
    # - +0.10 si detecta peso
    # - +0.10 si detecta marca
    # - +0.05 si detecta categoria
    # - +0.05 si score alto (>=3)
    conf = 0.55
    if peso:
        conf += 0.10
    if marca:
        conf += 0.10
    if categoria:
        conf += 0.05
    if best_score >= 3:
        conf += 0.05
    if conf > 0.85:
        conf = 0.85

    normalized = {
        "codigo_barra": barcode,
        "nombre": title,
        "marca": marca,
        "categoria": categoria,
        "descripcion": snippet,
        "peso_volumen": peso,
        "imagen_url": _norm_string(image_url),
        "fuente_datos": "serpapi_heuristic",
        "nivel_confianza": round(float(conf), 2),
    }

    useful = any(
        normalized.get(k) not in (None, "", "-")
        for k in ("nombre", "descripcion", "marca", "categoria", "peso_volumen", "imagen_url")
    )

    note = f"best_score={best_score}"
    # warning si hay 2 candidatos muy cerca
    if len(scored) > 1 and (scored[0][0] - scored[1][0]) <= 0:
        note += " (multiple_candidates)"

    trace.append({"provider": "heuristic_extractor", "ok": True, "found": bool(useful), "note": note})
    return normalized if useful else None


# ============================================================
# ✅ Providers externos (sin cambios, salvo hardening OFF timeout)
# ============================================================

def _lookup_openfoodfacts(barcode: str, trace: list) -> Optional[Dict[str, Any]]:
    url = _OFF_URL.format(barcode=barcode)
    headers = {"User-Agent": "ERPWeb/1.0 (smart-lookup)"}

    try:
        # ✅ MEJORA 2: timeout hardenizado (evita esperas largas)
        timeout = httpx.Timeout(connect=3.0, read=4.0, write=4.0, pool=4.0)
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            r = client.get(url, headers=headers)
            r.raise_for_status()
            payload = r.json()

        if payload.get("status") != 1:
            trace.append({"provider": "openfoodfacts", "ok": True, "found": False, "note": "status!=1"})
            return None

        product = payload.get("product") or {}
        normalized = {
            "codigo_barra": barcode,
            "nombre": _norm_string(product.get("product_name")),
            "marca": _norm_string(product.get("brands")),
            "categoria": _norm_string(product.get("categories")),
            "descripcion": _norm_string(product.get("generic_name")) or _norm_string(product.get("ingredients_text")),
            "peso_volumen": _norm_string(product.get("quantity")),
            "imagen_url": _norm_string(product.get("image_url")),
            "fuente_datos": "openfoodfacts",
            "nivel_confianza": 0.90,
        }

        useful = any(
            normalized.get(k) not in (None, "", "-")
            for k in ("nombre", "marca", "categoria", "descripcion", "peso_volumen", "imagen_url")
        )
        trace.append({"provider": "openfoodfacts", "ok": True, "found": bool(useful), "note": "parsed"})
        return normalized if useful else None

    except httpx.HTTPStatusError as e:
        status_code = getattr(getattr(e, "response", None), "status_code", None)
        trace.append({"provider": "openfoodfacts", "ok": False, "error": "HTTPStatusError", "status_code": status_code})
        return None
    except httpx.RequestError as e:
        trace.append({"provider": "openfoodfacts", "ok": False, "error": "RequestError", "detail": str(e)})
        return None
    except Exception as e:
        trace.append({"provider": "openfoodfacts", "ok": False, "error": "Exception", "detail": str(e)})
        return None


def _lookup_upcitemdb_trial(barcode: str, trace: list) -> Optional[Dict[str, Any]]:
    url = f"{_UPCITEMDB_TRIAL_URL}?upc={barcode}"
    headers = {"User-Agent": "ERPWeb/1.0 (smart-lookup)"}

    try:
        with httpx.Client(timeout=8.0, follow_redirects=True) as client:
            r = client.get(url, headers=headers)
            r.raise_for_status()
            payload = r.json()

        items = payload.get("items") or []
        if not items:
            trace.append({"provider": "upcitemdb_trial", "ok": True, "found": False, "note": "no_items"})
            return None

        item0 = items[0] or {}

        title = _norm_string(item0.get("title"))
        brand = _norm_string(item0.get("brand"))
        category = _norm_string(item0.get("category"))
        description = _norm_string(item0.get("description"))

        images = item0.get("images") or []
        image_url = _norm_string(images[0]) if images else None

        normalized = {
            "codigo_barra": barcode,
            "nombre": title,
            "marca": brand,
            "categoria": category,
            "descripcion": description,
            "peso_volumen": None,
            "imagen_url": image_url,
            "fuente_datos": "upcitemdb_trial",
            "nivel_confianza": 0.75,
        }

        useful = any(
            normalized.get(k) not in (None, "", "-")
            for k in ("nombre", "marca", "categoria", "descripcion", "imagen_url")
        )
        trace.append({"provider": "upcitemdb_trial", "ok": True, "found": bool(useful), "note": "parsed"})
        return normalized if useful else None

    except httpx.HTTPStatusError as e:
        status_code = getattr(getattr(e, "response", None), "status_code", None)
        trace.append({"provider": "upcitemdb_trial", "ok": False, "error": "HTTPStatusError", "status_code": status_code})
        return None
    except httpx.RequestError as e:
        trace.append({"provider": "upcitemdb_trial", "ok": False, "error": "RequestError", "detail": str(e)})
        return None
    except Exception as e:
        trace.append({"provider": "upcitemdb_trial", "ok": False, "error": "Exception", "detail": str(e)})
        return None


def _lookup_serpapi_google(barcode: str, trace: list) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """
    SerpAPI (Google engine) – prioritaria.
    Devuelve:
      (candidate_normalized_or_None, evidence_dict)
    """
    serp_key = getattr(settings, "SERPAPI_KEY", None) or os.getenv("SERPAPI_KEY")
    gl = getattr(settings, "SMART_LOOKUP_GL", None) or "ar"
    hl = getattr(settings, "SMART_LOOKUP_HL", None) or "es"

    evidence: Dict[str, Any] = {"query": None, "top_results": []}

    if not serp_key:
        trace.append({"provider": "serpapi", "ok": False, "found": False, "note": "missing_key"})
        return None, evidence

    # Dos intentos como máximo (no quemar cuota):
    # 1) estilo “barcode producto”
    # 2) “barcode” a secas
    queries = [
        f"{barcode} producto",
        f"\"{barcode}\"",
    ]

    headers = {"User-Agent": "ERPWeb/1.0 (smart-lookup)"}

    for idx, q in enumerate(queries, start=1):
        evidence = {"query": q, "top_results": []}

        params = {
            "engine": "google",
            "q": q,
            "hl": hl,
            "gl": gl,
            "api_key": serp_key,
            "num": 5,
        }

        try:
            with httpx.Client(timeout=12.0, follow_redirects=True) as client:
                r = client.get(_SERPAPI_URL, params=params, headers=headers)
                r.raise_for_status()
                payload = r.json()

            organic = payload.get("organic_results") or []
            top = []
            for item in organic[:5]:
                title = _norm_string(item.get("title"))
                snippet = _norm_string(item.get("snippet"))
                link = _norm_string(item.get("link"))
                source = _norm_string(item.get("source")) or _norm_string(item.get("displayed_link"))
                # algunos results traen thumbnail, no siempre
                thumb = _norm_string(item.get("thumbnail")) or _norm_string(item.get("image"))
                if title or snippet or link:
                    top.append({
                        "title": title,
                        "snippet": snippet,
                        "link": link,
                        "source": source,
                        "thumbnail": thumb,
                    })

            evidence["top_results"] = top

            found = len(top) > 0
            trace.append({"provider": "serpapi", "ok": True, "found": found, "note": f"organic_results (try {idx})"})

            if not found:
                continue

            # Candidate conservador (base mínima)
            first = top[0]
            candidate = {
                "codigo_barra": barcode,
                "nombre": first.get("title"),
                "marca": None,
                "categoria": None,
                "descripcion": first.get("snippet"),
                "peso_volumen": None,
                "imagen_url": first.get("thumbnail"),
                "fuente_datos": "serpapi",
                "nivel_confianza": 0.60,
            }

            useful = any(candidate.get(k) not in (None, "", "-") for k in ("nombre", "descripcion"))
            return (candidate if useful else None), evidence

        except httpx.HTTPStatusError as e:
            status_code = getattr(getattr(e, "response", None), "status_code", None)
            trace.append({"provider": "serpapi", "ok": False, "error": "HTTPStatusError", "status_code": status_code, "try": idx})
            continue
        except httpx.RequestError as e:
            trace.append({"provider": "serpapi", "ok": False, "error": "RequestError", "detail": str(e), "try": idx})
            continue
        except Exception as e:
            trace.append({"provider": "serpapi", "ok": False, "error": "Exception", "detail": str(e), "try": idx})
            continue

    return None, evidence


@login_required
@require_permission("stock.product.create")
@require_http_methods(["POST"])
@csrf_exempt  # Technical API (JSON) - CSRF intentionally disabled per API_RULES.md
def smart_product_lookup(request):
    """
    POST /api/stock/products/smart-lookup/
    Body: {"barcode": "<sku/ean/upc>", "force": true|false}

    Devuelve:
      SmartLookupResponse con data normalizada y fuentes.
    """
    body = _json_body(request)
    barcode = (body.get("barcode") or "").strip()
    force = bool(body.get("force", False))

    if not barcode:
        return JsonResponse({"detail": "Campo requerido: barcode"}, status=400)

    if not _is_probable_barcode(barcode):
        return JsonResponse({"detail": "barcode inválido (longitud/formato)"}, status=400)

    key = _cache_key(barcode)

    # 1) Cache (Django cache) - salvo que force=True
    if not force:
        cached_payload = cache.get(key)
        if cached_payload:
            cached_payload = dict(cached_payload)
            cached_payload["cached"] = True
            return JsonResponse(cached_payload, status=200)

    trace: List[Dict[str, Any]] = []
    sources: List[Dict[str, Any]] = []

    best: Dict[str, Any] = {
        "codigo_barra": barcode,
        "nombre": None,
        "marca": None,
        "categoria": None,
        "descripcion": None,
        "peso_volumen": None,
        "imagen_url": None,
        "fuente_datos": None,
        "nivel_confianza": None,
    }

    # 2) Provider 1: SerpAPI (Google)
    serp_candidate, serp_evidence = _lookup_serpapi_google(barcode, trace)
    sources.append({"type": "api", "name": "SerpAPI (Google)", "url": _SERPAPI_URL})
    if serp_candidate:
        best = _merge_best(best, serp_candidate)

    # 2b) Heuristic extractor (desde evidencia SerpAPI; NO IA; no pisa datos)
    heur_candidate = None
    if serp_evidence.get("top_results"):
        heur_candidate = _heuristic_extract_from_evidence(barcode, serp_evidence, trace)
        sources.append({"type": "rule", "name": "Heuristic extractor", "url": "local://heuristic"})
        if heur_candidate:
            best = _merge_best(best, heur_candidate)

    # 3) Provider 2: OpenFoodFacts (fallback)
    off = _lookup_openfoodfacts(barcode, trace)
    sources.append({"type": "api", "name": "OpenFoodFacts", "url": _OFF_URL.format(barcode=barcode)})
    if off:
        best = _merge_best(best, off)

        # ✅ PASO 1: precedencia OpenFoodFacts > heurística/serpapi en campos clave
        # Corrige errores típicos como categoría mal inferida.
        best = _apply_source_precedence(
            best,
            off,
            source_name="openfoodfacts",
            fields=("marca", "categoria", "peso_volumen", "imagen_url"),
            trace=trace,
        )

    # 4) Provider 3: UPCItemDB trial (fallback)
    upc = _lookup_upcitemdb_trial(barcode, trace)
    sources.append({"type": "api", "name": "UPCItemDB (trial)", "url": f"{_UPCITEMDB_TRIAL_URL}?upc={barcode}"})
    if upc:
        best = _merge_best(best, upc)

    # 5) FOUND / NOT_FOUND
    useful = any(
        best.get(k) not in (None, "", "-")
        for k in ("nombre", "marca", "categoria", "descripcion", "peso_volumen", "imagen_url")
    )

    if not useful:
        best["fuente_datos"] = "not_found"
        best["nivel_confianza"] = None

        suggested, missing = _compute_suggested_and_missing(best)
        payload = _smart_response(
            barcode=barcode,
            data=best,
            suggested_fields=suggested,
            missing_fields=missing,
            sources=sources,
            cached=False,
            warnings=[
                "No se encontró información suficiente.",
                "Tip: probá force=true para reintentar sin cache.",
                "Si SERPAPI_KEY no está configurada, se degradará a fallbacks estructurados.",
            ],
            evidence=serp_evidence if serp_evidence.get("top_results") else None,
            debug_trace=trace,
        )
        cache.set(key, payload, timeout=SMART_LOOKUP_NEG_TTL_SECONDS)
        return JsonResponse(payload, status=200)

    # Fuente prioritaria (preferimos heuristic sobre serpapi, luego OFF, luego UPC)
    if heur_candidate and any(heur_candidate.get(k) for k in ("nombre", "marca", "categoria", "descripcion", "peso_volumen", "imagen_url")):
        best["fuente_datos"] = "serpapi_heuristic"
        best["nivel_confianza"] = best.get("nivel_confianza") or (heur_candidate.get("nivel_confianza") or 0.70)
    elif serp_candidate and serp_candidate.get("nombre"):
        best["fuente_datos"] = "serpapi"
        best["nivel_confianza"] = best.get("nivel_confianza") or 0.60
    elif off and off.get("nombre"):
        best["fuente_datos"] = "openfoodfacts"
        best["nivel_confianza"] = best.get("nivel_confianza") or 0.90
    elif upc and upc.get("nombre"):
        best["fuente_datos"] = "upcitemdb_trial"
        best["nivel_confianza"] = best.get("nivel_confianza") or 0.75
    else:
        best["fuente_datos"] = best.get("fuente_datos") or "mixed"
        best["nivel_confianza"] = best.get("nivel_confianza") or 0.60

    suggested, missing = _compute_suggested_and_missing(best)
    payload = _smart_response(
        barcode=barcode,
        data=best,
        suggested_fields=suggested,
        missing_fields=missing,
        sources=sources,
        cached=False,
        warnings=[],
        evidence=serp_evidence if serp_evidence.get("top_results") else None,
        debug_trace=trace,
    )
    cache.set(key, payload, timeout=SMART_LOOKUP_TTL_SECONDS)
    return JsonResponse(payload, status=200)


# ============================================================
# ✅ API existente (sin cambios)
# ============================================================

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
