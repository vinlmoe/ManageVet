from django.urls import path

from . import views

app_name = "decisions"

urlpatterns = [
    path("clinics/<int:pk>/decisions/", views.decision_board, name="board"),
]
