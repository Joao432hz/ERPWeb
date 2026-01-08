from django.http import HttpResponseForbidden

class AdminSuperuserOnlyMiddleware:
    """
    Permite /admin/ solo a superusers.
    Cualquier usuario autenticado que NO sea superuser => 403.
    (Y si no está logueado, Django admin maneja su propio login.)
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/admin/"):
            user = request.user
            if user.is_authenticated and not user.is_superuser:
                return HttpResponseForbidden("Admin restricted to superusers.")
        return self.get_response(request)
