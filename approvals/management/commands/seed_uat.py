"""Jeu de données de démonstration pour la revue globale (UAT) avant livraison.

Crée/rafraîchit des comptes couvrant chaque rôle (demandeur, approbateurs à
2 niveaux, remplaçant en délégation, admin fonctionnel, super admin), des
types de demande avec leurs règles (dont un cas de conflit volontaire pour
démontrer la résolution par spécificité), et une délégation active.

Idempotent : peut être relancé sans dupliquer les données.
"""
import datetime

from django.contrib.auth.models import Group, Permission, User
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.utils import timezone

from approvals.models import (
    ApprovalLog,
    ApprovalRule,
    Delegation,
    Request,
    RequestType,
    UserProfile,
)


class Command(BaseCommand):
    help = "Crée le jeu de données de démonstration pour la revue globale (UAT)."

    def handle(self, *args, **options):
        def make_user(username, password, **fields):
            u, _ = User.objects.get_or_create(username=username, defaults=fields)
            for key, value in fields.items():
                setattr(u, key, value)
            u.set_password(password)
            u.save()
            return u

        admin = make_user(
            "admin", "Admin!2026", is_staff=True, is_superuser=True,
            first_name="Super", last_name="Admin",
        )

        group_admins_fonctionnels, _ = Group.objects.get_or_create(name="Admins fonctionnels")
        admin_fonctionnel = make_user(
            "admin_fonctionnel", "AdminFonc!2026", is_staff=True, is_superuser=False,
            first_name="Alice", last_name="Fonctionnel",
        )
        admin_fonctionnel.groups.add(group_admins_fonctionnels)

        managed_models = (RequestType, ApprovalRule, Delegation, UserProfile)
        for model in managed_models:
            ct = ContentType.objects.get_for_model(model)
            perms = Permission.objects.filter(content_type=ct)
            admin_fonctionnel.user_permissions.add(*perms)
            group_admins_fonctionnels.permissions.add(*perms)
        for model in (Request, ApprovalLog):
            ct = ContentType.objects.get_for_model(model)
            view_perms = Permission.objects.filter(content_type=ct, codename__startswith="view_")
            admin_fonctionnel.user_permissions.add(*view_perms)
            group_admins_fonctionnels.permissions.add(*view_perms)
        admin_fonctionnel.user_permissions.add(
            Permission.objects.get(
                content_type=ContentType.objects.get_for_model(Request), codename="change_request"
            )
        )

        director1 = make_user("director1", "Director!2026", first_name="Diane", last_name="Directrice")
        director1_delegate = make_user(
            "director1_delegate", "Delegate!2026", first_name="Damien", last_name="Delegue"
        )
        manager1 = make_user("manager1", "Manager!2026", first_name="Marc", last_name="Manager")
        employee1 = make_user("employee1", "Employee!2026", first_name="Emma", last_name="Employee")
        employee2 = make_user("employee2", "Employee2!2026", first_name="Eric", last_name="Employe")

        comite, _ = Group.objects.get_or_create(name="Comite de direction")
        comite.user_set.add(director1, director1_delegate)

        UserProfile.objects.update_or_create(
            user=employee1,
            defaults={"manager": manager1, "department_id": 10, "site_id": 1, "country_code": "FR"},
        )
        UserProfile.objects.update_or_create(
            user=employee2,
            defaults={"manager": manager1, "department_id": 20, "site_id": 2, "country_code": "FR"},
        )
        UserProfile.objects.update_or_create(user=manager1, defaults={"manager": director1, "department_id": 10})

        Delegation.objects.update_or_create(
            delegator=director1, delegate=director1_delegate,
            defaults={
                "start_date": timezone.localdate() - datetime.timedelta(days=1),
                "end_date": timezone.localdate() + datetime.timedelta(days=30),
                "scope": {},
            },
        )

        rt_expense, _ = RequestType.objects.update_or_create(
            code="EXPENSE",
            defaults={
                "name": "Note de frais",
                "is_active": True,
                "default_currency": "EUR",
                "form_schema": {"fields": [
                    {"name": "montant", "type": "decimal", "label": "Montant", "required": True},
                    {"name": "motif", "type": "text", "label": "Motif", "required": True},
                    {"name": "date_depense", "type": "date", "label": "Date de la dépense", "required": False},
                ]},
            },
        )
        ApprovalRule.objects.update_or_create(
            request_type=rt_expense, level=1, criteria={},
            defaults={"approvers_config": {"type": "manager"}, "created_by": admin},
        )
        ApprovalRule.objects.update_or_create(
            request_type=rt_expense, level=2, criteria={"min_amount": 1000},
            defaults={"approvers_config": {"type": "user", "user_id": director1.id}, "created_by": admin},
        )

        rt_leave, _ = RequestType.objects.update_or_create(
            code="LEAVE",
            defaults={
                "name": "Congés",
                "is_active": True,
                "form_schema": {"fields": [
                    {"name": "date_debut", "type": "date", "label": "Date de début", "required": True},
                    {"name": "date_fin", "type": "date", "label": "Date de fin", "required": True},
                    {"name": "motif", "type": "text", "label": "Motif (optionnel)", "required": False},
                ]},
            },
        )
        ApprovalRule.objects.update_or_create(
            request_type=rt_leave, level=1, criteria={},
            defaults={"approvers_config": {"type": "manager"}, "created_by": admin},
        )

        rt_purchase, _ = RequestType.objects.update_or_create(
            code="PURCHASE_IT",
            defaults={
                "name": "Achat Fournisseur IT",
                "is_active": True,
                "form_schema": {"fields": [
                    {"name": "montant", "type": "decimal", "label": "Montant HT", "required": True},
                    {"name": "fournisseur", "type": "text", "label": "Fournisseur", "required": True},
                ]},
            },
        )
        # Conflit volontaire : deux règles actives au même niveau (démonstration
        # de la résolution par spécificité, cf. ApprovalRuleAdmin "Conflit potentiel").
        ApprovalRule.objects.update_or_create(
            request_type=rt_purchase, level=1, criteria={"department_ids": [10]},
            defaults={"approvers_config": {"type": "group", "group_id": comite.id}, "created_by": admin},
        )
        ApprovalRule.objects.update_or_create(
            request_type=rt_purchase, level=1, criteria={},
            defaults={"approvers_config": {"type": "manager"}, "created_by": admin},
        )

        self.stdout.write(self.style.SUCCESS("Jeu de données UAT créé/rafraîchi."))
        self.stdout.write("Comptes : admin, admin_fonctionnel, director1, director1_delegate, manager1, employee1, employee2")
        self.stdout.write("Types de demande : EXPENSE, LEAVE, PURCHASE_IT")
