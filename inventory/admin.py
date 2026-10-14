from django.contrib import admin

from .models import ProductLine, StockItem


@admin.register(ProductLine)
class ProductLineAdmin(admin.ModelAdmin):
    list_display = ("name", "species", "unit_cost", "recommended_price", "units_per_animal")


@admin.register(StockItem)
class StockItemAdmin(admin.ModelAdmin):
    list_display = ("clinic", "product_line", "quantity", "target_quantity", "retail_price", "average_unit_cost")
    list_filter = ("clinic__server",)
