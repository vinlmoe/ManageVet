from django.urls import path

from . import views

app_name = "game"

urlpatterns = [
    path("", views.server_list, name="server_list"),
    path("servers/<int:pk>/", views.server_map, name="server_map"),
    path("clinics/<int:pk>/", views.clinic_dashboard, name="clinic_dashboard"),
]
