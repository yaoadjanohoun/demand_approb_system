"""Rapports et statistiques :
volume de demandes par mois, taux de refus par type, temps moyen
d'approbation par type et par département.
"""
import csv

from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q
from django.db.models.functions import TruncMonth
from django.http import HttpResponse

from .models import Request

MONTH_NAMES_FR = [
    "", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
]

_DURATION_EXPR = ExpressionWrapper(
    F("completed_at") - F("submitted_at"), output_field=DurationField()
)


def _submitted_requests():
    """Demandes réellement entrées dans le circuit (exclut les brouillons)."""
    return Request.objects.exclude(status=Request.Status.DRAFT).exclude(submitted_at__isnull=True)


def _duration_hours(td):
    if td is None:
        return None
    return round(td.total_seconds() / 3600, 1)


def _with_percentages(rows, key):
    values = [r[key] for r in rows if r[key] is not None]
    top = max(values) if values else 0
    for r in rows:
        r["pct"] = round(r[key] / top * 100) if top and r[key] is not None else 0
    return rows


def volume_by_month():
    rows = (
        _submitted_requests()
        .annotate(month=TruncMonth("submitted_at"))
        .values("month")
        .annotate(count=Count("id"))
        .order_by("month")
    )
    result = [
        {"label": f"{MONTH_NAMES_FR[r['month'].month]} {r['month'].year}", "count": r["count"]}
        for r in rows
    ]
    return _with_percentages(result, "count")


def rejection_rate_by_type():
    finished = _submitted_requests().filter(
        status__in=[Request.Status.APPROVED, Request.Status.REJECTED]
    )
    rows = (
        finished.values("request_type__name")
        .annotate(
            total=Count("id"),
            rejected=Count("id", filter=Q(status=Request.Status.REJECTED)),
        )
        .order_by("request_type__name")
    )
    result = [
        {
            "name": r["request_type__name"],
            "total": r["total"],
            "rejected": r["rejected"],
            "rate": round(r["rejected"] / r["total"] * 100, 1) if r["total"] else 0,
        }
        for r in rows
    ]
    for r in result:
        r["pct"] = r["rate"]
    return result


def average_approval_time_by_type():
    rows = (
        _submitted_requests()
        .filter(status=Request.Status.APPROVED, completed_at__isnull=False)
        .annotate(duration=_DURATION_EXPR)
        .values("request_type__name")
        .annotate(avg_duration=Avg("duration"), count=Count("id"))
        .order_by("request_type__name")
    )
    result = [
        {"name": r["request_type__name"], "count": r["count"], "avg_hours": _duration_hours(r["avg_duration"])}
        for r in rows
    ]
    return _with_percentages(result, "avg_hours")


def average_approval_time_by_department():
    rows = (
        _submitted_requests()
        .filter(status=Request.Status.APPROVED, completed_at__isnull=False)
        .annotate(duration=_DURATION_EXPR)
        .values("requester__profile__department__name")
        .annotate(avg_duration=Avg("duration"), count=Count("id"))
        .order_by("requester__profile__department__name")
    )
    result = [
        {
            "department": r["requester__profile__department__name"] or "Non renseigné",
            "count": r["count"],
            "avg_hours": _duration_hours(r["avg_duration"]),
        }
        for r in rows
    ]
    return _with_percentages(result, "avg_hours")


def export_requests_csv():
    """Export brut de toutes les demandes, pour analyse dans Excel/Power BI."""
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="demandes.csv"'
    writer = csv.writer(response)
    writer.writerow([
        "id", "type", "demandeur", "departement", "statut", "niveau_courant",
        "soumise_le", "terminee_le", "duree_heures",
    ])
    qs = Request.objects.select_related("request_type", "requester", "requester__profile", "requester__profile__department")
    for req in qs:
        duration = None
        if req.submitted_at and req.completed_at:
            duration = _duration_hours(req.completed_at - req.submitted_at)
        profile = getattr(req.requester, "profile", None)
        department = profile.department.name if profile and profile.department else None
        writer.writerow([
            req.id, req.request_type.code, req.requester.username,
            department if department is not None else "",
            req.status, req.current_level,
            req.submitted_at.isoformat() if req.submitted_at else "",
            req.completed_at.isoformat() if req.completed_at else "",
            duration if duration is not None else "",
        ])
    return response
