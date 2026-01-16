from django.urls import path
from . import views

urlpatterns = [
    # Productos
    path("products/", views.products_list, name="stock_products_list"),

    # ✅ NUEVO: Smart Lookup de productos (alta asistida)
    # POST /api/stock/products/smart-lookup/
    path(
        "products/smart-lookup/",
        views.smart_product_lookup,
        name="stock_products_smart_lookup",
    ),

    # Movimientos de stock
    path("movements/", views.movements_list, name="stock_movements_list"),
    path("movements/create/", views.movement_create, name="stock_movement_create"),
]
