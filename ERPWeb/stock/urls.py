from django.urls import path
from . import views

urlpatterns = [
    path("products/", views.products_list, name="stock_products_list"),
    path("movements/", views.movements_list, name="stock_movements_list"),
    path("movements/create/", views.movement_create, name="stock_movement_create"),
]
