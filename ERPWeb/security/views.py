import json

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User

from .decorators import require_permission
from .models import Role, Permission, UserRole, RolePermission


def _json_body(request):
    """
    Lee JSON del body. Si está vacío o es inválido, devuelve {}.
    """
    try:
        raw = request.body.decode("utf-8") if request.body else ""
        return json.loads(raw or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}


# -------------------------
# LISTADOS BÁSICOS
# -------------------------

@login_required
@require_permission("security.role.view")
@require_http_methods(["GET"])
def roles_list(request):
    data = list(Role.objects.values("id", "name", "description", "is_active"))
    return JsonResponse({"roles": data})


@login_required
@require_permission("security.permission.view")
@require_http_methods(["GET"])
def permissions_list(request):
    data = list(Permission.objects.values("id", "code", "description"))
    return JsonResponse({"permissions": data})


# ✅ DECISIÓN: my_permissions abierto para cualquier usuario autenticado
# (sin require_permission)
@login_required
@require_http_methods(["GET"])
def my_permissions(request):
    """
    Devuelve los códigos de permisos efectivos del usuario logueado,
    en base a roles asignados (UserRole) y permisos por rol (RolePermission).

    Endpoint de introspección personal: solo devuelve permisos del propio usuario.
    """
    role_ids = UserRole.objects.filter(user=request.user).values_list("role_id", flat=True)
    perms = RolePermission.objects.filter(role_id__in=role_ids).select_related("permission")
    codes = sorted({rp.permission.code for rp in perms})
    return JsonResponse({"user": request.user.username, "permissions": codes})


# -------------------------
# VISTAS PROTEGIDAS (Operador / Pruebas)
# -------------------------

# Vista de test ligada a permiso de operador (dashboard)
@login_required
@require_permission("security.dashboard.view")
@require_http_methods(["GET"])
def test_protected_view(request):
    """
    Vista de prueba: sirve para validar que el usuario tiene acceso de operador.
    """
    return JsonResponse({
        "status": "ok",
        "message": "Tenés permiso para ver esta vista (operador)"
    })


# Dashboard con permiso específico por responsabilidad
@login_required
@require_permission("security.dashboard.view")
@require_http_methods(["GET"])
def dashboard_view(request):
    return JsonResponse({
        "status": "ok",
        "user": request.user.username,
        "message": "Bienvenido al dashboard (vista de operador)",
        "next_steps": [
            "Stock",
            "Compras",
            "Ventas",
            "Finanzas",
            "Reportes",
        ],
    })


# -------------------------
# CRUD ROLES
# -------------------------

@login_required
@require_permission("security.role.create")
@require_http_methods(["POST"])
def role_create(request):
    data = _json_body(request)

    name = (data.get("name") or "").strip()
    description = (data.get("description") or "").strip()
    is_active = bool(data.get("is_active", True))

    if not name:
        return JsonResponse({"detail": "name is required"}, status=400)

    role, created = Role.objects.get_or_create(
        name=name,
        defaults={"description": description, "is_active": is_active},
    )

    if not created:
        return JsonResponse({"detail": "Role already exists"}, status=409)

    return JsonResponse({
        "role": {
            "id": role.id,
            "name": role.name,
            "description": role.description,
            "is_active": role.is_active
        }
    }, status=201)


@login_required
@require_permission("security.role.update")
@require_http_methods(["POST"])
def role_update(request, role_id: int):
    data = _json_body(request)

    try:
        role = Role.objects.get(id=role_id)
    except Role.DoesNotExist:
        return JsonResponse({"detail": "Role not found"}, status=404)

    if "name" in data:
        new_name = (data.get("name") or "").strip()
        if not new_name:
            return JsonResponse({"detail": "name cannot be empty"}, status=400)
        role.name = new_name

    if "description" in data:
        role.description = (data.get("description") or "").strip()

    if "is_active" in data:
        role.is_active = bool(data.get("is_active"))

    role.save()

    return JsonResponse({
        "role": {
            "id": role.id,
            "name": role.name,
            "description": role.description,
            "is_active": role.is_active
        }
    })


@login_required
@require_permission("security.role.delete")
@require_http_methods(["POST"])
def role_delete(request, role_id: int):
    try:
        role = Role.objects.get(id=role_id)
    except Role.DoesNotExist:
        return JsonResponse({"detail": "Role not found"}, status=404)

    role.delete()
    return JsonResponse({"status": "ok", "deleted_role_id": role_id})


# -------------------------
# PERMISOS POR ROL
# -------------------------

@login_required
@require_permission("security.permission.view")
@require_http_methods(["GET"])
def role_permissions(request, role_id: int):
    try:
        role = Role.objects.get(id=role_id)
    except Role.DoesNotExist:
        return JsonResponse({"detail": "Role not found"}, status=404)

    perms = Permission.objects.filter(rolepermission__role=role).values("id", "code", "description")

    return JsonResponse({
        "role": {"id": role.id, "name": role.name},
        "permissions": list(perms)
    })


@login_required
@require_permission("security.permission.manage")
@require_http_methods(["POST"])
def role_permission_add(request, role_id: int):
    data = _json_body(request)
    perm_code = (data.get("permission_code") or "").strip()

    if not perm_code:
        return JsonResponse({"detail": "permission_code is required"}, status=400)

    try:
        role = Role.objects.get(id=role_id)
    except Role.DoesNotExist:
        return JsonResponse({"detail": "Role not found"}, status=404)

    try:
        perm = Permission.objects.get(code=perm_code)
    except Permission.DoesNotExist:
        return JsonResponse({"detail": "Permission not found"}, status=404)

    RolePermission.objects.get_or_create(role=role, permission=perm)
    return JsonResponse({"status": "ok"})


@login_required
@require_permission("security.permission.manage")
@require_http_methods(["POST"])
def role_permission_remove(request, role_id: int):
    data = _json_body(request)
    perm_code = (data.get("permission_code") or "").strip()

    if not perm_code:
        return JsonResponse({"detail": "permission_code is required"}, status=400)

    RolePermission.objects.filter(role_id=role_id, permission__code=perm_code).delete()
    return JsonResponse({"status": "ok"})


# -------------------------
# ROLES POR USUARIO
# -------------------------

@login_required
@require_permission("security.userrole.assign")
@require_http_methods(["GET"])
def user_roles(request, user_id: int):
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({"detail": "User not found"}, status=404)

    roles = Role.objects.filter(userrole__user=user).values("id", "name", "description", "is_active")

    return JsonResponse({
        "user": {"id": user.id, "username": user.username},
        "roles": list(roles)
    })


@login_required
@require_permission("security.userrole.assign")
@require_http_methods(["POST"])
def user_role_add(request, user_id: int):
    data = _json_body(request)
    role_id = data.get("role_id")

    if not role_id:
        return JsonResponse({"detail": "role_id is required"}, status=400)

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({"detail": "User not found"}, status=404)

    try:
        role = Role.objects.get(id=role_id)
    except Role.DoesNotExist:
        return JsonResponse({"detail": "Role not found"}, status=404)

    UserRole.objects.get_or_create(user=user, role=role)
    return JsonResponse({"status": "ok"})


@login_required
@require_permission("security.userrole.assign")
@require_http_methods(["POST"])
def user_role_remove(request, user_id: int):
    data = _json_body(request)
    role_id = data.get("role_id")

    if not role_id:
        return JsonResponse({"detail": "role_id is required"}, status=400)

    UserRole.objects.filter(user_id=user_id, role_id=role_id).delete()
    return JsonResponse({"status": "ok"})

