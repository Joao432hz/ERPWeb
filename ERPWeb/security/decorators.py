from functools import wraps
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse


# Paths que SIEMPRE deben ser públicos (sin RBAC)
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


def require_permission(perm_code: str):
    """
    RBAC decorator:
    - Si el path es público (login/logout/static/etc), no aplica RBAC.
    - Si no está autenticado:
        - Para requests tipo browser (HTML): redirect a login con next
        - Para API: 401 JSON
    - Si no tiene permiso: 403 JSON (como ya tenés validado)
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            # ✅ 0) Whitelist público (crítico para que login funcione)
            if _is_public_path(getattr(request, "path", "")):
                return view_func(request, *args, **kwargs)

            user = getattr(request, "user", None)

            # ✅ 1) No autenticado
            if not user or not user.is_authenticated:
                # Heurística simple: si el cliente espera HTML, redirigimos
                accept = (request.headers.get("Accept") or "").lower()
                if "text/html" in accept or "*/*" in accept:
                    login_url = reverse("login") if "login" in [u.name for u in request.resolver_match.app_names] else "/accounts/login/"
                    return redirect(f"{login_url}?next={request.path}")
                return JsonResponse({"detail": "Unauthorized"}, status=401)

            # ✅ 2) Superuser: pasa todo
            if getattr(user, "is_superuser", False):
                return view_func(request, *args, **kwargs)

            # ✅ 3) Validación RBAC (tu lógica actual)
            # IMPORTANTE: acá respetamos tu arquitectura: roles -> permisos
            try:
                from security.models import UserRole, RolePermission, Permission
            except Exception:
                return JsonResponse({"detail": "RBAC not available"}, status=500)

            has_perm = RolePermission.objects.filter(
                role__userrole__user=user,
                permission__code=perm_code,
                role__is_active=True,
            ).exists()

            if not has_perm:
                return JsonResponse({"detail": "Forbidden - missing permission"}, status=403)

            return view_func(request, *args, **kwargs)

        return _wrapped
    return decorator
