from django import template

register = template.Library()


def _user_has_permission(user, perm_key: str) -> bool:
    """
    Wrapper seguro para tu RBAC.
    - Intenta usar funciones existentes del proyecto (según cómo lo tengas armado).
    - Si no encuentra nada, cae a fallback seguro (False, excepto superuser).
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False

    # Superuser Django siempre puede
    if getattr(user, "is_superuser", False):
        return True

    # Intento 1: función directa en security.decorators (común en tu proyecto)
    try:
        from security.decorators import user_has_permission  # type: ignore
        return bool(user_has_permission(user, perm_key))
    except Exception:
        pass

    # Intento 2: función en security.services (por si la tenés ahí)
    try:
        from security.services import user_has_permission  # type: ignore
        return bool(user_has_permission(user, perm_key))
    except Exception:
        pass

    # Intento 3: si tu User implementa has_perm (Django-like)
    try:
        if hasattr(user, "has_perm"):
            return bool(user.has_perm(perm_key))
    except Exception:
        pass

    return False


@register.simple_tag
def can(user, perm_key: str) -> bool:
    """
    Uso en templates:
      {% load rbac %}
      {% can request.user "stock.product.view" as ok %}
      {% if ok %} ... {% endif %}
    """
    return _user_has_permission(user, perm_key)
