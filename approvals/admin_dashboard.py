"""Alimente le tableau de bord admin (page d'accueil /admin/, retour client :
"un dashboard pour l'administrateur comme pour les utilisateurs, avec des
charts"). Réutilise les mêmes fonctions que la page Rapports côté client
(approvals/reports.py) pour rester cohérent avec elle.

Les graphiques utilisent les composants natifs d'Unfold
(unfold/components/chart/bar.html, un wrapper Chart.js) plutôt que du CSS
maison, pour s'intégrer visuellement à l'admin (thème clair/sombre inclus :
les couleurs "var(--color-...)" sont résolues dynamiquement par le JS
d'Unfold selon le thème actif)."""
import json

from django.contrib.auth.models import Group, User
from django.urls import reverse
from django.utils import timezone

from . import admin_permissions
from . import reports as reports_module
from .models import Delegation, Department, Request, RequestType, Site


def _chart_json(labels, values, color):
    return json.dumps({
        "labels": labels,
        "datasets": [{"data": values, "backgroundColor": color, "maxBarThickness": 48}],
    })


# (icône, titre, description, url_name, permission_callback) — même filtrage que la
# sidebar (retour client) : ces cartes ne doivent pas offrir de liens vers des
# sections que l'utilisateur n'a pas la permission d'utiliser.
_QUICK_LINKS = [
    ("description", "Types de demandes", "Formulaires et champs proposés",
     "admin:approvals_requesttype_changelist", admin_permissions.can_view_requesttype),
    ("rule", "Règles d'approbation", "Qui approuve quoi, à quel niveau",
     "admin:approvals_approvalrule_changelist", admin_permissions.can_view_approvalrule),
    ("apartment", "Départements", "Référentiel utilisé par les règles",
     "admin:approvals_department_changelist", admin_permissions.can_view_department),
    ("location_on", "Sites", "Référentiel utilisé par les règles",
     "admin:approvals_site_changelist", admin_permissions.can_view_site),
    ("groups", "Groupes", "Créer et organiser des groupes",
     "admin:auth_group_changelist", admin_permissions.can_view_group),
    ("monitoring", "Rapports détaillés", "Statistiques complètes, export CSV",
     "approvals:reports", admin_permissions.can_view_reports),
]


def dashboard_callback(request, context):
    volume = reports_module.volume_by_month()
    rejection = reports_module.rejection_rate_by_type()
    duration_by_type = reports_module.average_approval_time_by_type()
    duration_by_department = reports_module.average_approval_time_by_department()

    quick_links = [
        {"icon": icon, "title": title, "description": description, "url": reverse(url_name)}
        for icon, title, description, url_name, can_view in _QUICK_LINKS
        if can_view(request)
    ]

    context.update({
        "quick_links": quick_links,
        "can_view_reports": admin_permissions.can_view_reports(request),
        "admin_summary": reports_module.summary_stats(),
        "admin_counts": {
            "active_users": User.objects.filter(is_active=True).count(),
            "pending_requests": Request.objects.filter(status=Request.Status.PENDING).count(),
            "active_request_types": RequestType.objects.filter(is_active=True).count(),
            "groups": Group.objects.count(),
            "departments": Department.objects.count(),
            "sites": Site.objects.count(),
            "active_delegations": Delegation.objects.filter(
                start_date__lte=timezone.localdate(), end_date__gte=timezone.localdate()
            ).count(),
        },
        "admin_chart_volume": _chart_json(
            [r["label_short"] for r in volume], [r["count"] for r in volume], "var(--color-primary-600)"
        ),
        "admin_chart_rejection": _chart_json(
            [r["label"] for r in rejection], [r["rate"] for r in rejection], "var(--color-red-600)"
        ),
        "admin_chart_duration_type": _chart_json(
            [r["label"] for r in duration_by_type], [r["avg_hours"] for r in duration_by_type],
            "var(--color-primary-600)",
        ),
        "admin_chart_duration_department": _chart_json(
            [r["label"] for r in duration_by_department], [r["avg_hours"] for r in duration_by_department],
            "var(--color-primary-600)",
        ),
        "admin_has_volume": bool(volume),
        "admin_has_rejection": bool(rejection),
        "admin_has_duration_type": bool(duration_by_type),
        "admin_has_duration_department": bool(duration_by_department),
    })
    return context
