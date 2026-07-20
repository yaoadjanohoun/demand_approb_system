"""Tests du backend d'authentification Active Directory.

Utilise un faux serveur LDAP (ldap3.Connection est mocké) plutôt que le
mode MOCK_SYNC natif de ldap3 : notre backend ouvre plusieurs connexions
successives (compte de service, vérification du mot de passe, résolution
du manager) et MOCK_SYNC ne partage pas son annuaire simulé entre connexions
distinctes sans bricolage. Un mock ciblé sur ldap3.Connection est plus simple
et teste la même logique (filtre de recherche, résolution du DN, mapping
des attributs, gestion des échecs).
"""
from unittest.mock import MagicMock, patch

import ldap3
from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from .auth_backends import ActiveDirectoryBackend
from .models import UserProfile

SERVICE_DN = "svc_django"
SERVICE_PASSWORD = "service-password"

JDUPONT_DN = "CN=Jean Dupont,OU=Users,DC=example,DC=local"
MMARTIN_DN = "CN=Marie Martin,OU=Users,DC=example,DC=local"
JDUPONT_PASSWORD = "correcthorsebattery"

DIRECTORY = {
    JDUPONT_DN: {
        "sAMAccountName": "jdupont",
        "givenName": "Jean",
        "sn": "Dupont",
        "mail": "jean.dupont@example.local",
        "manager": MMARTIN_DN,
        "department": "IT",
        "physicalDeliveryOfficeName": "Paris",
    },
    MMARTIN_DN: {
        "sAMAccountName": "mmartin",
        "givenName": "Marie",
        "sn": "Martin",
        "mail": "marie.martin@example.local",
    },
}


class _FakeAttr:
    def __init__(self, value):
        self.value = value


class _FakeEntry:
    def __init__(self, dn, attrs):
        self.entry_dn = dn
        self._attrs = attrs

    def __contains__(self, key):
        return key in self._attrs

    def __getitem__(self, key):
        return _FakeAttr(self._attrs[key])


def _make_fake_connection(server, user=None, password=None, auto_bind=None, **kwargs):
    expected_password = SERVICE_PASSWORD if user == SERVICE_DN else None
    if expected_password is None:
        for dn in DIRECTORY:
            if user == dn:
                expected_password = JDUPONT_PASSWORD if dn == JDUPONT_DN else "irrelevant"
    if user == SERVICE_DN:
        if password != SERVICE_PASSWORD:
            raise ldap3.core.exceptions.LDAPBindError("invalid service credentials")
    elif user == JDUPONT_DN:
        if password != JDUPONT_PASSWORD:
            raise ldap3.core.exceptions.LDAPBindError("invalid credentials")
    else:
        raise ldap3.core.exceptions.LDAPBindError("unknown dn")

    conn = MagicMock()
    conn.entries = []

    def fake_search(search_base, search_filter="(objectClass=*)", attributes=None, search_scope=None):
        conn.entries = []
        if search_scope == ldap3.BASE:
            attrs = DIRECTORY.get(search_base)
            if attrs:
                conn.entries = [_FakeEntry(search_base, attrs)]
            return bool(conn.entries)

        for dn, attrs in DIRECTORY.items():
            sam = attrs.get("sAMAccountName")
            if sam and sam in search_filter:
                conn.entries = [_FakeEntry(dn, attrs)]
                return True
        return False

    conn.search = MagicMock(side_effect=fake_search)
    return conn


LDAP_TEST_SETTINGS = dict(
    AUTH_LDAP_SERVER_URI="ldap://fake-ad.example.local",
    AUTH_LDAP_BIND_DN=SERVICE_DN,
    AUTH_LDAP_BIND_PASSWORD=SERVICE_PASSWORD,
    AUTH_LDAP_USER_SEARCH_BASE="OU=Users,DC=example,DC=local",
    AUTH_LDAP_USER_SEARCH_FILTER="(sAMAccountName={username})",
    AUTH_LDAP_USERNAME_ATTR="sAMAccountName",
    AUTH_LDAP_ATTR_MAP={
        "first_name": "givenName",
        "last_name": "sn",
        "email": "mail",
        "manager": "manager",
        "department": "department",
        "site": "physicalDeliveryOfficeName",
    },
)


@override_settings(**LDAP_TEST_SETTINGS)
@patch("approvals.auth_backends.ldap3.Connection", side_effect=_make_fake_connection)
class ActiveDirectoryBackendTests(TestCase):
    def setUp(self):
        self.backend = ActiveDirectoryBackend()

    def test_successful_login_creates_user_with_no_usable_password(self, _mock_conn):
        user = self.backend.authenticate(None, username="jdupont", password=JDUPONT_PASSWORD)
        self.assertIsNotNone(user)
        self.assertEqual(user.username, "jdupont")
        self.assertEqual(user.first_name, "Jean")
        self.assertEqual(user.last_name, "Dupont")
        self.assertEqual(user.email, "jean.dupont@example.local")
        self.assertFalse(user.has_usable_password())  # aucun mot de passe interne stocké

    def test_successful_login_syncs_profile(self, _mock_conn):
        manager = User.objects.create_user("mmartin", password="unused")
        user = self.backend.authenticate(None, username="jdupont", password=JDUPONT_PASSWORD)
        profile = UserProfile.objects.get(user=user)
        self.assertEqual(profile.department_name, "IT")
        self.assertEqual(profile.site_name, "Paris")
        self.assertEqual(profile.manager_id, manager.id)
        self.assertIsNotNone(profile.last_ad_sync)

    def test_manager_not_linked_if_manager_has_no_django_account_yet(self, _mock_conn):
        user = self.backend.authenticate(None, username="jdupont", password=JDUPONT_PASSWORD)
        profile = UserProfile.objects.get(user=user)
        self.assertIsNone(profile.manager)

    def test_wrong_password_returns_none(self, _mock_conn):
        self.assertIsNone(
            self.backend.authenticate(None, username="jdupont", password="wrong-password")
        )
        self.assertFalse(User.objects.filter(username="jdupont").exists())

    def test_unknown_username_returns_none(self, _mock_conn):
        self.assertIsNone(
            self.backend.authenticate(None, username="ghost", password="whatever")
        )

    def test_missing_credentials_returns_none(self, _mock_conn):
        self.assertIsNone(self.backend.authenticate(None, username="jdupont", password=""))
        self.assertIsNone(self.backend.authenticate(None, username="", password="x"))

    def test_repeated_login_updates_existing_user_instead_of_duplicating(self, _mock_conn):
        first = self.backend.authenticate(None, username="jdupont", password=JDUPONT_PASSWORD)
        second = self.backend.authenticate(None, username="jdupont", password=JDUPONT_PASSWORD)
        self.assertEqual(first.id, second.id)
        self.assertEqual(User.objects.filter(username="jdupont").count(), 1)


@override_settings(AUTH_LDAP_SERVER_URI="")
class ActiveDirectoryBackendDisabledTests(TestCase):
    def test_returns_none_when_ldap_not_configured(self):
        backend = ActiveDirectoryBackend()
        self.assertIsNone(backend.authenticate(None, username="jdupont", password="whatever"))
