from django.urls import path
from . import views

app_name = "ui"

urlpatterns = [
    # Dashboard UI (root)
    path("", views.dashboard, name="dashboard"),
    path("forbidden/", views.forbidden, name="forbidden"),

    # ✅ UI bajo /ui/ para NO pisar endpoints API (/stock, /purchases, /sales, /finance)
    # Stock
    path("ui/stock/products/", views.stock_products, name="stock_products"),
    path("ui/stock/movements/", views.stock_movements, name="stock_movements"),

    # Compras / Ventas / Finanzas (UI mínimo)
    path("ui/purchases/orders/", views.purchases_orders, name="purchases_orders"),
    path("ui/purchases/orders/new/", views.purchases_order_create, name="purchases_order_create"),
    path("ui/sales/orders/", views.sales_orders, name="sales_orders"),
    path("ui/finance/movements/", views.finance_movements, name="finance_movements"),

    # ✅ Compras: detalle + acciones (UI)
    path("ui/purchases/orders/<int:pk>/", views.purchases_order_detail, name="purchases_order_detail"),
    path("ui/purchases/orders/<int:pk>/confirm/", views.purchases_order_confirm, name="purchases_order_confirm"),
    path("ui/purchases/orders/<int:pk>/receive/", views.purchases_order_receive, name="purchases_order_receive"),
    path("ui/purchases/orders/<int:pk>/cancel/", views.purchases_order_cancel, name="purchases_order_cancel"),

    # ✅ API UI: products autocomplete + detail
    path("ui/api/products/search/", views.products_search, name="products_search"),
    path("ui/api/products/<int:pk>/", views.product_detail, name="product_detail"),
]
