from django.urls import path
from . import views

urlpatterns = [
    path("suppliers/", views.suppliers_list, name="purchases_suppliers_list"),

    path("orders/", views.purchase_orders_list, name="purchases_orders_list"),
    path("orders/<int:po_id>/", views.purchase_order_detail, name="purchases_order_detail"),

    path("orders/create/", views.purchase_order_create, name="purchases_order_create"),

    path("orders/<int:po_id>/lines/add/", views.purchase_order_add_line, name="purchases_order_add_line"),
    path("orders/<int:po_id>/lines/<int:line_id>/update/", views.purchase_order_update_line, name="purchases_order_update_line"),
    path("orders/<int:po_id>/lines/<int:line_id>/delete/", views.purchase_order_delete_line, name="purchases_order_delete_line"),

    path("orders/<int:po_id>/confirm/", views.purchase_order_confirm, name="purchases_order_confirm"),
    path("orders/<int:po_id>/receive/", views.purchase_order_receive, name="purchases_order_receive"),
    path("orders/<int:po_id>/cancel/", views.purchase_order_cancel, name="purchases_order_cancel"),
]
