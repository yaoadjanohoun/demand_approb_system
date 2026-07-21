"""
URL configuration for demand_approb_system_main project.

"""
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from approvals import auth_views as approvals_auth_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/', approvals_auth_views.login_view, name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),
    path(
        'mot-de-passe/reinitialiser/',
        auth_views.PasswordResetView.as_view(
            template_name='registration/password_reset_form.html',
            email_template_name='registration/password_reset_email.txt',
            subject_template_name='registration/password_reset_subject.txt',
        ),
        name='password_reset',
    ),
    path(
        'mot-de-passe/reinitialiser/envoye/',
        auth_views.PasswordResetDoneView.as_view(template_name='registration/password_reset_done.html'),
        name='password_reset_done',
    ),
    path(
        'mot-de-passe/reinitialiser/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(template_name='registration/password_reset_confirm.html'),
        name='password_reset_confirm',
    ),
    path(
        'mot-de-passe/reinitialiser/termine/',
        auth_views.PasswordResetCompleteView.as_view(template_name='registration/password_reset_complete.html'),
        name='password_reset_complete',
    ),
    path('', include('approvals.urls')),
]
