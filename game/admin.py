from django.contrib import admin

from .models import GameServer, PopulationZone


@admin.register(GameServer)
class GameServerAdmin(admin.ModelAdmin):
    list_display = ("name", "status", "current_quarter", "total_quarters", "next_resolution_at")
    list_filter = ("status",)


@admin.register(PopulationZone)
class PopulationZoneAdmin(admin.ModelAdmin):
    list_display = ("server", "x", "y", "dogs", "cats", "price_sensitivity")
    list_filter = ("server",)
