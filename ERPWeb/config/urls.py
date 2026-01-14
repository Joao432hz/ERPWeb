from django.contrib import admin
from django.urls import path, include

from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # Django Admin
    path("admin/", admin.site.urls),

    # Auth (login / logout)
    path("accounts/", include("django.contrib.auth.urls")),

    # Apps del sistema (API / módulos) — mantener lo validado
    # Security no colisiona con UI (no usamos /security/* en UI)
    path("security/", include("security.urls")),

    # ✅ APIs que colisionan con UI: mover bajo /api/
    path("api/sales/", include("sales.urls")),
    path("api/finance/", include("finance.urls")),
    path("api/stock/", include("stock.urls")),
    path("api/purchases/", include("purchases.urls")),

    # UI (Frontend ERP) - Root "/" → Dashboard UI
    # ✅ Dejar al FINAL
    path("", include(("ui.urls", "ui"), namespace="ui")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
