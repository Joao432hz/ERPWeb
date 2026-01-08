from django.urls import path
from . import views

app_name = "sales"

urlpatterns = [
    # List + detail
    path("orders/", views.sales_orders_list, name="sales_orders_list"),
    path("orders/<int:so_id>/", views.sales_order_detail, name="sales_order_detail"),

    # Create
    path("orders/create/", views.sales_order_create, name="sales_order_create"),

    # Lines (DRAFT only)
    path("orders/<int:so_id>/lines/add/", views.sales_order_add_line, name="sales_order_add_line"),
    path("orders/<int:so_id>/lines/<int:line_id>/update/", views.sales_order_update_line, name="sales_order_update_line"),
    path("orders/<int:so_id>/lines/<int:line_id>/delete/", views.sales_order_delete_line, name="sales_order_delete_line"),

    # Workflow
    path("orders/<int:so_id>/confirm/", views.sales_order_confirm, name="sales_order_confirm"),
    path("orders/<int:so_id>/cancel/", views.sales_order_cancel, name="sales_order_cancel"),
]
