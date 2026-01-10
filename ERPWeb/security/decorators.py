from functools import wraps
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

PUBLIC_PATH_PREFIXES = (
    "/accounts/login/",
    "/accounts/logout/",
    "/admin/login/",
    "/static/",
    "/media/",
)

def _is_public_path(path: str) -> bool:
    if not path:
        return False
    return any(path.startswith(p) for p in PUBLIC_PATH_PREFIXES)

def _wants_html(request) -> bool:
    """
    Determina si el cliente espera HTML.
    Regla segura:
    - Si Accept incluye text/html => HTML
    - Si Accept está vacío => asumimos HTML (navegadores pueden no enviar Accept en casos raros)
    - En caso contrario => JSON (APIs / integraciones)
    """
    accept = (request.headers.get("Accept") or "").lower().strip()
    if not accept:
        return True
    return "text/html" in accept

def require_permission(perm_code: str):
    """
    RBAC decorator:
    - Public paths: no aplica RBAC
    - No autenticado:
        - HTML => redirect login con next
        - API  => 401 JSON
    - Sin permiso:
        - HTML => forbidden.html (403)
        - API  => 403 JSON
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if _is_public_path(getattr(request, "path", "")):
                return view_func(request, *args, **kwargs)

            user = getattr(request, "user", None)

            # 1) No autenticado
            if not user or not user.is_authenticated:
                if _wants_html(request):
                    login_url = reverse("login")
                    return redirect(f"{login_url}?next={request.path}")
                return JsonResponse({"detail": "Unauthorized"}, status=401)

            # 2) Superuser: pasa todo
            if getattr(user, "is_superuser", False):
                return view_func(request, *args, **kwargs)

            # 3) Validación RBAC
            try:
                from security.models import RolePermission
            except Exception:
                return JsonResponse({"detail": "RBAC not available"}, status=500)

            has_perm = RolePermission.objects.filter(
                role__userrole__user=user,
                permission__code=perm_code,
                role__is_active=True,
            ).exists()

            if not has_perm:
                if _wants_html(request):
                    return render(
                        request,
                        "ui/forbidden.html",
                        {"required_permission": perm_code},
                        status=403,
                    )
                return JsonResponse({"detail": "Forbidden - missing permission"}, status=403)

            return view_func(request, *args, **kwargs)

        return _wrapped
    return decorator
