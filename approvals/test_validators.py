"""Validateurs de "vrais noms" (Prénom/Nom, Département, Site, Type de
demande, Groupe, config email) — retour test déploiement : des caractères
individuellement autorisés (lettres, chiffres) peuvent quand même former du
remplissage n'importe quoi via une longue répétition, ex: "Ip Fictive0000000000000005"."""
from django.core.exceptions import ValidationError
from django.test import TestCase

from .validators import validate_entity_name, validate_person_name


class ValidatePersonNameTests(TestCase):
    def test_normal_names_accepted(self):
        for name in ["Jean-Paul", "O'Brien", "Éric", "Marie Curie"]:
            validate_person_name(name)  # ne doit pas lever

    def test_stray_symbols_rejected(self):
        with self.assertRaises(ValidationError):
            validate_person_name("Super////////")

    def test_no_letter_at_all_rejected(self):
        with self.assertRaises(ValidationError):
            validate_person_name("1234")

    def test_excessive_letter_repetition_rejected(self):
        with self.assertRaises(ValidationError):
            validate_person_name("Aaaaaaaaaa")

    def test_short_legitimate_repetition_accepted(self):
        validate_person_name("Mississippi")  # "ss"/"pp" : jamais plus de 2 d'affilée


class ValidateEntityNameTests(TestCase):
    def test_normal_business_names_accepted(self):
        for name in ["R&D", "Site 12", "Gmail (test)", "IT-Support", "Ventes, Nord"]:
            validate_entity_name(name)  # ne doit pas lever

    def test_symbol_spam_rejected(self):
        with self.assertRaises(ValidationError):
            validate_entity_name("test///////////////////*****************-*******!!!!!")

    def test_no_letter_or_digit_at_all_rejected(self):
        with self.assertRaises(ValidationError):
            validate_entity_name("----...")

    def test_excessive_digit_repetition_rejected(self):
        """Cas exact du retour test : "Ip Fictive0000000000000005" — chaque
        caractère pris isolément est autorisé (lettres, chiffres, espace),
        mais la longue suite de "0" trahit une saisie de test, pas un vrai
        nom de site."""
        with self.assertRaises(ValidationError):
            validate_entity_name("Ip Fictive0000000000000005")

    def test_three_repeated_letters_still_accepted(self):
        validate_entity_name("AAA Assurances")  # sigle plausible, 3 d'affilée
