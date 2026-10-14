from django.contrib import admin

from .models import Clinic, ClinicEquipment, ClinicService, EquipmentType, ServiceType, StaffMember


class ClinicServiceInline(admin.TabularInline):
    model = ClinicService
    extra = 0


class StaffInline(admin.TabularInline):
    model = StaffMember
    extra = 0


class EquipmentInline(admin.TabularInline):
    model = ClinicEquipment
    extra = 0


@admin.register(Clinic)
class ClinicAdmin(admin.ModelAdmin):
    list_display = ("name", "server", "owner", "x", "y", "cash", "reputation")
    list_filter = ("server",)
    readonly_fields = ("cash",)  # modifiée uniquement par des écritures comptables
    inlines = [ClinicServiceInline, StaffInline, EquipmentInline]


@admin.register(ServiceType)
class ServiceTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "reference_price", "frequency_per_animal", "vet_minutes", "asv_minutes", "required_equipment")


@admin.register(EquipmentType)
class EquipmentTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "purchase_price", "lease_per_quarter", "useful_life_quarters")
