from django.urls import path

from ui import views

app_name = "ui"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("forbidden/", views.forbidden, name="forbidden"),

    # Stock
    path("stock/products/", views.stock_products, name="stock_products"),
    path("stock/products/new/", views.stock_product_create, name="stock_product_create"),
    path("stock/products/<int:pk>/", views.stock_product_detail, name="stock_product_detail"),

    # ✅ NUEVO: Editar producto (mantiene ID)
    path("stock/products/<int:pk>/edit/", views.stock_product_edit, name="stock_product_edit"),

    path("stock/movements/", views.stock_movements, name="stock_movements"),

    # ✅ Movimientos por producto
    path("stock/products/<int:pk>/movements/", views.stock_product_movements, name="stock_product_movements"),

    # ✅ Etiquetas por producto
    path("stock/products/<int:pk>/labels/", views.stock_product_labels, name="stock_product_labels"),

    # ✅ Imágenes (PNG) para barcode / QR
    path("stock/products/<int:pk>/barcode.png", views.stock_product_barcode_png, name="stock_product_barcode_png"),
    path("stock/products/<int:pk>/qr.png", views.stock_product_qr_png, name="stock_product_qr_png"),

    # Compras - Órdenes
    path("purchases/orders/", views.purchases_orders, name="purchases_orders"),
    path("purchases/orders/new/", views.purchases_order_create, name="purchases_order_create"),
    path("purchases/orders/<int:pk>/", views.purchases_order_detail, name="purchases_order_detail"),
    path("purchases/orders/<int:pk>/confirm/", views.purchases_order_confirm, name="purchases_order_confirm"),
    path("purchases/orders/<int:pk>/receive/", views.purchases_order_receive, name="purchases_order_receive"),
    path("purchases/orders/<int:pk>/cancel/", views.purchases_order_cancel, name="purchases_order_cancel"),

    # Compras - Proveedores
    path("purchases/suppliers/", views.purchases_suppliers, name="purchases_suppliers"),
    path("purchases/suppliers/new/", views.purchases_supplier_create, name="purchases_supplier_create"),
    path("purchases/suppliers/<int:pk>/", views.purchases_supplier_detail, name="purchases_supplier_detail"),
    path("purchases/suppliers/<int:pk>/edit/", views.purchases_supplier_edit, name="purchases_supplier_edit"),

    # UI API - Autocomplete productos
    path("api/products/search/", views.products_search, name="products_search"),
    path("api/products/<int:pk>/", views.product_detail, name="product_detail"),

    # Ventas / Finanzas
    path("sales/orders/", views.sales_orders, name="sales_orders"),
    path("finance/movements/", views.finance_movements, name="finance_movements"),
]
