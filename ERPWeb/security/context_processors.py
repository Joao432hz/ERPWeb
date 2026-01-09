from typing import Dict, List
from django.http import HttpRequest

from .models import RolePermission


def perm_keys(request: HttpRequest) -> Dict[str, List[str]]:
    """
    Inyecta perm_keys en TODOS los templates.
    - Si no hay user o no está autenticado => []
    - Superuser => [] (no hace falta, el template ya chequea is_superuser)
    - Caso normal => lista de códigos de permisos por RBAC
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {"perm_keys": []}

    if getattr(user, "is_superuser", False):
        return {"perm_keys": []}

    qs = (
        RolePermission.objects.filter(
            role__userrole__user=user,
            role__is_active=True,
        )
        .values_list("permission__code", flat=True)
        .distinct()
    )

    return {"perm_keys": list(qs)}
