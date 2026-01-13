from django.contrib import admin
from django.urls import path, include

from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # UI (Frontend ERP) - Root "/" → Dashboard UI
    path("", include(("ui.urls", "ui"), namespace="ui")),

    # Django Admin
    path("admin/", admin.site.urls),

    # Auth (login / logout)
    path("accounts/", include("django.contrib.auth.urls")),

    # Apps del sistema
    path("security/", include("security.urls")),
    path("stock/", include("stock.urls")),
    path("purchases/", include("purchases.urls")),
    path("sales/", include("sales.urls")),
    path("finance/", include("finance.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
