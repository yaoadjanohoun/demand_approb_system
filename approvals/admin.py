from django.contrib import admin

from .models import ApprovalLog, ApprovalRule, Delegation, Request, RequestType, UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "manager", "department_id", "site_id", "country_code")
    search_fields = ("user__username",)


@admin.register(RequestType)
class RequestTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_active", "schema_version", "resume_on_resubmit")
    list_filter = ("is_active", "is_sensitive")
    search_fields = ("name", "code")


@admin.register(ApprovalRule)
class ApprovalRuleAdmin(admin.ModelAdmin):
    list_display = ("request_type", "level", "is_active", "created_by", "updated_at")
    list_filter = ("request_type", "is_active", "level")


@admin.register(Request)
class RequestAdmin(admin.ModelAdmin):
    list_display = ("id", "request_type", "requester", "status", "current_level", "submitted_at")
    list_filter = ("status", "request_type")
    search_fields = ("id", "requester__username")


@admin.register(Delegation)
class DelegationAdmin(admin.ModelAdmin):
    list_display = ("delegator", "delegate", "start_date", "end_date", "is_active")
    list_filter = ("start_date", "end_date")


@admin.register(ApprovalLog)
class ApprovalLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "request", "actor", "action_type", "previous_status", "new_status")
    list_filter = ("action_type",)
    readonly_fields = [f.name for f in ApprovalLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
