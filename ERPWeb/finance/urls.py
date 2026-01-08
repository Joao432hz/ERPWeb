from django.urls import path
from . import views

urlpatterns = [
    # List / BI
    path("movements/", views.financial_movements_list, name="financial_movements_list"),
    path("summary/", views.financial_summary, name="financial_summary"),
    path("export/", views.financial_export_csv, name="financial_export_csv"),

    # Actions
    path("movements/<int:movement_id>/pay/", views.financial_movement_pay, name="financial_movement_pay"),
]
