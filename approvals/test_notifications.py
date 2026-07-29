"""Notifications email sur les événements du workflow (retour client) :
soumission, décisions (approuvée/refusée/retournée), et interventions admin."""
from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase

from .models import ApprovalRule, Request, RequestType, UserProfile
from .services import WorkflowEngine


class NotificationTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user("manager1", password="x", email="manager1@example.com")
        self.director = User.objects.create_user("director1", password="x", email="director1@example.com")
        self.employee = User.objects.create_user("employee1", password="x", email="employee1@example.com")
        self.admin = User.objects.create_user(
            "admin_fonc", password="x", email="admin@example.com", is_staff=True
        )
        UserProfile.objects.create(user=self.employee, manager=self.manager)

        self.request_type = RequestType.objects.create(
            name="Note de frais", code="EXPENSE",
            form_schema={"fields": [{"name": "montant", "type": "decimal", "required": True}]},
        )
        ApprovalRule.objects.create(
            request_type=self.request_type, level=1, criteria={}, approvers_config={"type": "manager"}
        )
        ApprovalRule.objects.create(
            request_type=self.request_type, level=2, criteria={"min_amount": 1000},
            approvers_config={"type": "user", "user_id": self.director.id},
        )

    def make_request(self, montant):
        return Request.objects.create(
            request_type=self.request_type, requester=self.employee, data={"montant": montant},
        )

    def test_submission_notifies_requester_approvers_and_staff(self):
        req = self.make_request(1500)
        WorkflowEngine(req).submit()

        recipients = {r for m in mail.outbox for r in m.to}
        self.assertIn("employee1@example.com", recipients)  # confirmation au demandeur
        self.assertIn("manager1@example.com", recipients)  # approbateur niveau 1
        self.assertIn("admin@example.com", recipients)  # staff

    def test_auto_approval_notifies_requester_when_no_rule_matches(self):
        other_type = RequestType.objects.create(
            name="Sans règle", code="NORULE", form_schema={"fields": []},
        )
        req = Request.objects.create(request_type=other_type, requester=self.employee, data={})
        WorkflowEngine(req).submit()

        req.refresh_from_db()
        self.assertEqual(req.status, Request.Status.APPROVED)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("employee1@example.com", mail.outbox[0].to)

    def test_level_advancement_notifies_next_approver(self):
        req = self.make_request(1500)
        WorkflowEngine(req).submit()
        mail.outbox.clear()

        WorkflowEngine(req).approve(self.manager)

        recipients = {r for m in mail.outbox for r in m.to}
        self.assertIn("director1@example.com", recipients)

    def test_final_approval_notifies_requester(self):
        req = self.make_request(500)  # un seul niveau
        WorkflowEngine(req).submit()
        mail.outbox.clear()

        WorkflowEngine(req).approve(self.manager)

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("employee1@example.com", mail.outbox[0].to)
        self.assertIn("approuvée", mail.outbox[0].subject)

    def test_rejection_notifies_requester_with_comment(self):
        req = self.make_request(500)
        WorkflowEngine(req).submit()
        mail.outbox.clear()

        WorkflowEngine(req).reject(self.manager, "budget dépassé")

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("employee1@example.com", mail.outbox[0].to)
        self.assertIn("budget dépassé", mail.outbox[0].body)

    def test_return_for_info_notifies_requester_with_comment(self):
        req = self.make_request(500)
        WorkflowEngine(req).submit()
        mail.outbox.clear()

        WorkflowEngine(req).return_for_info(self.manager, "pièce jointe manquante")

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("employee1@example.com", mail.outbox[0].to)
        self.assertIn("pièce jointe manquante", mail.outbox[0].body)

    def test_email_link_is_absolute_not_a_bare_path(self):
        """Retour client : le lien envoyé par email était un chemin relatif
        (ex: "/44a0825a-.../") — inutilisable depuis un client email, en
        dehors du navigateur où on était connecté. SITE_URL (settings.py)
        doit préfixer le lien."""
        from django.conf import settings

        req = self.make_request(500)
        WorkflowEngine(req).submit()

        requester_email = next(m for m in mail.outbox if "employee1@example.com" in m.to)
        self.assertIn(f"{settings.SITE_URL}/{req.pk}/", requester_email.body)
        self.assertNotIn(f" /{req.pk}/", requester_email.body)

    def test_notification_never_raises_when_recipient_has_no_email(self):
        req = self.make_request(500)
        WorkflowEngine(req).submit()
        self.manager.email = ""
        self.manager.save()
        mail.outbox.clear()

        # Ne doit pas lever, même si l'approbateur courant n'a pas d'email.
        WorkflowEngine(req).approve(self.manager)
        req.refresh_from_db()
        self.assertEqual(req.status, Request.Status.APPROVED)
