from django.urls import path

from . import views

app_name = "players"

urlpatterns = [
    path("accounts/signup/", views.signup, name="signup"),
    path("servers/<int:pk>/join/", views.join_server, name="join"),
]
