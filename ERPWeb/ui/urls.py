from django.urls import path
from . import views

app_name = "ui"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("forbidden/", views.forbidden, name="forbidden"),

    # Stock
    path("stock/products/", views.stock_products, name="stock_products"),
    path("stock/movements/", views.stock_movements, name="stock_movements"),

    # Compras / Ventas / Finanzas (UI mínimo)
    path("purchases/orders/", views.purchases_orders, name="purchases_orders"),
    path("sales/orders/", views.sales_orders, name="sales_orders"),
    path("finance/movements/", views.finance_movements, name="finance_movements"),

    # ✅ Compras: detalle + acciones (UI)
    path("purchases/orders/<int:pk>/", views.purchases_order_detail, name="purchases_order_detail"),
    path("purchases/orders/<int:pk>/confirm/", views.purchases_order_confirm, name="purchases_order_confirm"),
    path("purchases/orders/<int:pk>/receive/", views.purchases_order_receive, name="purchases_order_receive"),
    path("purchases/orders/<int:pk>/cancel/", views.purchases_order_cancel, name="purchases_order_cancel"),
]
