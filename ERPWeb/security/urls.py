from django.urls import path
from . import views

urlpatterns = [
    # Test / básicos
    path("test/", views.test_protected_view),
    path("me/permissions/", views.my_permissions),

    # ✅ NUEVO: Dashboard del operador
    path("dashboard/", views.dashboard_view, name="security_dashboard"),

    # Listados
    path("roles/list/", views.roles_list),
    path("permissions/list/", views.permissions_list),

    # CRUD Roles
    path("roles/create/", views.role_create),
    path("roles/update/<int:role_id>/", views.role_update),
    path("roles/delete/<int:role_id>/", views.role_delete),

    # Permisos por Rol
    path("roles/<int:role_id>/permissions/", views.role_permissions),
    path("roles/<int:role_id>/permissions/add/", views.role_permission_add),
    path("roles/<int:role_id>/permissions/remove/", views.role_permission_remove),

    # Roles por Usuario
    path("users/<int:user_id>/roles/", views.user_roles),
    path("users/<int:user_id>/roles/add/", views.user_role_add),
    path("users/<int:user_id>/roles/remove/", views.user_role_remove),
]

