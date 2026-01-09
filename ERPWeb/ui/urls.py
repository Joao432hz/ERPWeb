from django.urls import path
from . import views

app_name = "ui"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),

    path("forbidden/", views.forbidden, name="forbidden"),

    path("stock/products/", views.stock_products, name="stock_products"),
    path("stock/movements/", views.stock_movements, name="stock_movements"),
]
