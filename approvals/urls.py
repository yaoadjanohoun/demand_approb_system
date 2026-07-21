from django.urls import path

from . import auth_views, views

app_name = "approvals"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("inscription/", auth_views.register, name="register"),
    path("confirmer-email/<str:token>/", auth_views.confirm_email, name="confirm_email"),
    path("connexion/confirmer/<str:token>/", auth_views.confirm_login, name="confirm_login"),
    path("profil/", views.profile, name="profile"),
    path("mine/", views.my_requests, name="my_requests"),
    path("pending/", views.pending_approvals, name="pending_approvals"),
    path("rapports/", views.reports, name="reports"),
    path("rapports/export/", views.reports_export, name="reports_export"),
    path("new/<int:type_id>/", views.request_create, name="request_create"),
    path("<uuid:pk>/", views.request_detail, name="request_detail"),
    path("<uuid:pk>/edit/", views.request_edit, name="request_edit"),
    path("<uuid:pk>/delete/", views.request_delete, name="request_delete"),
    path("<uuid:pk>/approve/", views.request_approve, name="request_approve"),
    path("<uuid:pk>/reject/", views.request_reject, name="request_reject"),
    path("<uuid:pk>/return/", views.request_return, name="request_return"),
]
