from django.contrib import admin
from django.urls import path, include

from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # Django Admin
    path("admin/", admin.site.urls),

    # Auth (login / logout)
    path("accounts/", include("django.contrib.auth.urls")),

    # Apps del sistema (API / módulos)
    path("security/", include("security.urls")),
    path("stock/", include("stock.urls")),
    path("purchases/", include("purchases.urls")),
    path("sales/", include("sales.urls")),
    path("finance/", include("finance.urls")),

    # UI (Frontend ERP) - Root "/" → Dashboard UI
    # ✅ Dejar al FINAL para no pisar endpoints tipo /finance/*
    path("", include(("ui.urls", "ui"), namespace="ui")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
