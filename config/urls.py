from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("players.urls")),
    path("accounts/", include("django.contrib.auth.urls")),
    path("", include("decisions.urls")),
    path("", include("game.urls")),
]
