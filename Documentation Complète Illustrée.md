# Documentation Complète Illustrée — Système de Demandes et d'Approbation

Ce document présente l'application écran par écran, pour les trois rôles principaux
(demandeur, approbateur, administrateur fonctionnel). Il complète les documents
techniques existants (voir [README.md](README.md)) avec une visite guidée illustrée.

## Comment compléter ce document

Chaque capture d'écran attendue est indiquée par un bloc comme celui-ci :

> 📷 **Capture 01 — `01-connexion.png`**
> Ce qu'il faut voir : la page de connexion, vide.

Pour compléter le document :
1. Créer un dossier `images/` à la racine du projet (à côté de ce fichier).
2. Suivre les étapes indiquées dans le [Journal des captures](#journal-des-captures) en fin de document pour reproduire chaque écran.
3. Enregistrer chaque capture sous le nom exact indiqué (ex: `images/01-connexion.png`).
4. Les blocs `📷` ci-dessous se transforment automatiquement en images une fois les fichiers présents — aucune autre modification n'est nécessaire (les liens `![...](images/...)` sont déjà en place, en commentaire juste après chaque bloc).

Jeu de données utilisé pour toutes les captures : `python manage.py seed_uat` (voir [INSTALL.md](INSTALL.md)).

---

## 1. Côté demandeur (employé)

### 1.1. Connexion

Chaque utilisateur se connecte avec son compte local ou son compte Active Directory,
selon la configuration de l'environnement. Un lien "Afficher le mot de passe" permet
de vérifier sa saisie avant de valider.

> 📷 **Capture 01 — `01-connexion.png`**
> Page de connexion vide, avec la case "Afficher le mot de passe" visible.
<!-- ![Connexion](images/01-connexion.png) -->

### 1.2. Double authentification par email

Si l'administrateur a activé la double authentification (voir §3.7), un code à
9 chiffres est envoyé par email après la saisie du mot de passe. L'utilisateur le
saisit sur cette page pour finaliser sa connexion.

> 📷 **Capture 02 — `02-code-confirmation.png`**
> Page "Confirmer votre connexion" avec le champ de saisie du code.
<!-- ![Confirmation de connexion](images/02-code-confirmation.png) -->

### 1.3. Tableau de bord

Page d'accueil : chiffres clés (demandes en cours, en attente, à approuver) et accès
direct à chaque type de demande actif.

> 📷 **Capture 03 — `03-dashboard.png`**
> Tableau de bord d'un demandeur, avec au moins une demande dans chaque statut si possible.
<!-- ![Tableau de bord](images/03-dashboard.png) -->

### 1.4. Soumettre une nouvelle demande

Le formulaire est généré automatiquement à partir du type de demande choisi
(configuré par l'admin fonctionnel, voir §3.2). Chaque demande peut recevoir une ou
plusieurs pièces jointes (justificatif, facture, etc.).

> 📷 **Capture 04 — `04-nouvelle-demande.png`**
> Formulaire "Nouvelle demande" (ex: Note de frais), avec les champs remplis et la
> zone de pièce jointe visible.
<!-- ![Nouvelle demande](images/04-nouvelle-demande.png) -->

### 1.5. Enregistrer un brouillon

Une demande incomplète peut être enregistrée comme brouillon et reprise plus tard,
sans obligation de remplir tous les champs.

> 📷 **Capture 05 — `05-brouillon.png`**
> Page "Mes demandes" montrant une ligne au statut Brouillon, avec les liens
> "Continuer" et "Supprimer".
<!-- ![Brouillon](images/05-brouillon.png) -->

### 1.6. Détail d'une demande et pièces jointes

Chaque demande affiche son statut, son niveau d'approbation courant, ses pièces
jointes et son historique complet (qui a fait quoi, et quand).

> 📷 **Capture 06 — `06-detail-demande.png`**
> Détail d'une demande avec au moins une pièce jointe et un historique de plusieurs
> lignes (soumission + une décision).
<!-- ![Détail d'une demande](images/06-detail-demande.png) -->

### 1.7. Profil personnel

L'utilisateur peut modifier son nom d'utilisateur, son nom complet, son email et sa
photo de profil. Le manager, le département et le site restent en lecture seule
(gérés par un admin fonctionnel).

> 📷 **Capture 07 — `07-profil.png`**
> Page de profil, avec une photo de profil définie.
<!-- ![Profil](images/07-profil.png) -->

---

## 2. Côté approbateur (manager, directeur, comité)

### 2.1. Liste des demandes à approuver

Chaque approbateur ne voit que les demandes qui lui sont effectivement assignées au
niveau courant (y compris via une délégation active, voir §3.6).

> 📷 **Capture 08 — `08-a-approuver.png`**
> Liste "À approuver" avec au moins deux demandes de types différents.
<!-- ![À approuver](images/08-a-approuver.png) -->

### 2.2. Prendre une décision

Trois actions possibles : Approuver, Retourner pour information (avec commentaire
obligatoire), ou Refuser (avec motif obligatoire).

> 📷 **Capture 09 — `09-decision.png`**
> Détail d'une demande en attente, avec la carte "Décision" et ses trois boutons
> visibles sur une seule ligne.
<!-- ![Décision](images/09-decision.png) -->

### 2.3. Passer à la demande suivante

Après une décision, un lien permet d'enchaîner directement sur la prochaine demande
en attente du même type, sans repasser par la liste.

> 📷 **Capture 10 — `10-demande-suivante.png`**
> Carte "Suite" en bas d'une demande déjà traitée, montrant le lien vers la demande
> suivante.
<!-- ![Demande suivante](images/10-demande-suivante.png) -->

---

## 3. Côté administrateur fonctionnel

L'accès à l'administration se fait via le bouton "Administration" (visible
uniquement pour les comptes staff) ou directement sur `/admin/`.

### 3.1. Tableau de bord administrateur

Vue d'ensemble avec chiffres clés, accès rapide aux sections principales, et les
mêmes graphiques que la page Rapports côté client.

> 📷 **Capture 11 — `11-dashboard-admin.png`**
> Tableau de bord admin avec au moins un graphique affichant des données (pas l'état vide).
<!-- ![Tableau de bord admin](images/11-dashboard-admin.png) -->

### 3.2. Configurer un type de demande

Le constructeur visuel permet d'ajouter des champs (nom technique, label, type,
obligatoire) sans écrire de JSON à la main.

> 📷 **Capture 12 — `12-types-demandes.png`**
> Fiche d'édition d'un type de demande, avec au moins deux champs déjà ajoutés dans
> le constructeur visuel.
<!-- ![Types de demandes](images/12-types-demandes.png) -->

### 3.3. Configurer une règle d'approbation

Chaque règle définit qui approuve (utilisateur, groupe, ou manager du demandeur),
sous quelles conditions (montant, département, site...), à quel niveau.

> 📷 **Capture 13 — `13-regles-approbation.png`**
> Fiche d'édition d'une règle, avec le constructeur de critères et le mode de
> résolution des approbateurs visibles.
<!-- ![Règles d'approbation](images/13-regles-approbation.png) -->

### 3.4. Départements et sites

Référentiels nommés utilisés par les règles d'approbation et les profils
utilisateurs (plutôt que de simples identifiants numériques).

> 📷 **Capture 14 — `14-departements.png`**
> Liste des départements avec plusieurs entrées.
<!-- ![Départements](images/14-departements.png) -->

### 3.5. Groupes et permissions

L'admin fonctionnel peut créer ses propres groupes (ex: "Comité de vente") et leur
attribuer des permissions ciblées.

> 📷 **Capture 15 — `15-groupes.png`**
> Fiche d'édition d'un groupe, avec la liste des permissions disponibles/choisies.
<!-- ![Groupes](images/15-groupes.png) -->

### 3.6. Délégations (absences)

Une délégation temporaire transfère automatiquement les approbations d'un
utilisateur absent vers son remplaçant, pour la durée choisie.

> 📷 **Capture 16 — `16-delegations.png`**
> Liste des délégations, avec au moins une délégation active affichée comme telle.
<!-- ![Délégations](images/16-delegations.png) -->

### 3.7. Configuration email

La configuration SMTP (Gmail, Exchange, etc.) se change entièrement depuis l'admin,
sans toucher au code ni redéployer. C'est ici aussi qu'on active ou désactive la
double authentification par email (voir §1.2).

> 📷 **Capture 17 — `17-config-email.png`**
> Fiche de configuration email, avec le mot de passe masqué et la case "Double
> authentification requise" visible.
<!-- ![Configuration email](images/17-config-email.png) -->

---

## 4. Rapports et statistiques

Accessible aux comptes staff, avec un export CSV pour analyse externe (Excel, Power BI).

> 📷 **Capture 18 — `18-rapports.png`**
> Page Rapports avec les 4 graphiques affichant des données réelles (pas l'état vide).
<!-- ![Rapports](images/18-rapports.png) -->

---

## 5. Inscription en ligne

Un nouvel utilisateur peut créer son propre compte ; son adresse email doit être
confirmée, puis un administrateur fonctionnel doit activer le compte avant la
première connexion.

> 📷 **Capture 19 — `19-inscription.png`**
> Formulaire d'inscription, avec la case "Afficher le mot de passe" visible.
<!-- ![Inscription](images/19-inscription.png) -->

> 📷 **Capture 20 — `20-activation-compte.png`**
> Dans l'admin, la liste des profils utilisateurs avec l'action "Activer les comptes
> sélectionnés" visible, et un compte en attente d'activation (email confirmé,
> compte encore inactif).
<!-- ![Activation de compte](images/20-activation-compte.png) -->

---

## Journal des captures

Comptes de test disponibles après `python manage.py seed_uat` (voir le Manuel
d'Administration Fonctionnel pour la liste complète et les mots de passe).

| # | Fichier | Compte à utiliser | URL | Étapes |
|---|---|---|---|---|
| 01 | `01-connexion.png` | — | `/login/` | Page telle quelle, sans se connecter. |
| 02 | `02-code-confirmation.png` | employee1 | `/login/` | Activer d'abord la double authentification (§3.7) et renseigner un email sur le compte, puis se connecter. |
| 03 | `03-dashboard.png` | employee1 | `/` | Se connecter, soumettre 2-3 demandes de types différents au préalable. |
| 04 | `04-nouvelle-demande.png` | employee1 | `/new/1/` | Ouvrir "Note de frais", remplir les champs, sans soumettre. |
| 05 | `05-brouillon.png` | employee1 | `/mine/` | Enregistrer une demande comme brouillon au préalable (bouton "Enregistrer comme brouillon"). |
| 06 | `06-detail-demande.png` | employee1 | `/<id>/` | Sur une demande déjà approuvée avec une pièce jointe ajoutée à la soumission. |
| 07 | `07-profil.png` | employee1 | `/profil/` | Ajouter une photo de profil au préalable. |
| 08 | `08-a-approuver.png` | manager1 | `/pending/` | Avec au moins deux demandes en attente assignées à ce compte. |
| 09 | `09-decision.png` | manager1 | `/<id>/` | Sur une demande en attente à son niveau. |
| 10 | `10-demande-suivante.png` | manager1 | `/<id>/` | Après avoir approuvé une demande, sur une autre demande du même type encore en attente. |
| 11 | `11-dashboard-admin.png` | admin | `/admin/` | Avec des demandes déjà traitées dans le jeu de données. |
| 12 | `12-types-demandes.png` | admin_fonctionnel | `/admin/approvals/requesttype/1/change/` | Type "Note de frais" par exemple. |
| 13 | `13-regles-approbation.png` | admin_fonctionnel | `/admin/approvals/approvalrule/1/change/` | Une règle existante du jeu de données. |
| 14 | `14-departements.png` | admin_fonctionnel | `/admin/approvals/department/` | — |
| 15 | `15-groupes.png` | admin_fonctionnel | `/admin/auth/group/1/change/` | Groupe "Admins fonctionnels" ou "Comite de direction". |
| 16 | `16-delegations.png` | admin_fonctionnel | `/admin/approvals/delegation/` | La délégation director1 → director1_delegate du jeu de données. |
| 17 | `17-config-email.png` | admin (super admin) | `/admin/approvals/emailsettings/1/change/` | Réservé au super admin (voir §3.7). |
| 18 | `18-rapports.png` | admin_fonctionnel | `/rapports/` | Avec plusieurs demandes traitées dans le jeu de données. |
| 19 | `19-inscription.png` | — | `/inscription/` | Page telle quelle, sans être connecté. |
| 20 | `20-activation-compte.png` | admin_fonctionnel | `/admin/approvals/userprofile/` | Après qu'un compte se soit inscrit et ait confirmé son email (voir §5). |

**Note sur les emails** (captures 02 et l'email de confirmation d'inscription) : sans
configuration SMTP active dans l'admin, les emails s'affichent dans la console du
serveur (`python manage.py runserver`) plutôt que d'être réellement envoyés — le
code ou le lien s'y trouve directement, pas besoin d'une vraie boîte mail pour
prendre ces captures.
