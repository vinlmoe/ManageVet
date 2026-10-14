from django.contrib import admin

from .models import JournalEntry, JournalLine, QuarterReport


class JournalLineInline(admin.TabularInline):
    model = JournalLine
    extra = 0
    readonly_fields = ("account", "account_label", "debit", "credit")
    can_delete = False


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display = ("clinic", "quarter", "label", "created_at")
    list_filter = ("clinic__server", "quarter")
    inlines = [JournalLineInline]


@admin.register(QuarterReport)
class QuarterReportAdmin(admin.ModelAdmin):
    list_display = ("clinic", "quarter", "revenue_services", "revenue_goods", "net_result", "cash_end", "market_share")
    list_filter = ("clinic__server", "quarter")
