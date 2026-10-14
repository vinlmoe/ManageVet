from django.contrib import admin

from .models import PendingDecision


@admin.register(PendingDecision)
class PendingDecisionAdmin(admin.ModelAdmin):
    list_display = ("clinic", "quarter", "kind", "status", "message", "created_at")
    list_filter = ("status", "kind", "clinic__server")
