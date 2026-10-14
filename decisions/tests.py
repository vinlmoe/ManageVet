from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from clinics.services import create_clinic
from engine.resolution import resolve_quarter
from game.catalog import seed_catalog
from game.map import generate_zones
from game.models import GameServer

from .models import PendingDecision

Kind = PendingDecision.Kind
Status = PendingDecision.Status


class DecisionBoardTests(TestCase):
    def setUp(self):
        seed_catalog()
        User = get_user_model()
        self.server = GameServer.objects.create(name="Partie", grid_width=8, grid_height=8, seed=3)
        generate_zones(self.server, towns=1)
        self.player = User.objects.create_user("etudiant")
        self.rival = User.objects.create_user("rival")
        self.clinic = create_clinic(self.server, self.player, "Ma clinique", 2, 2)
        self.rival_clinic = create_clinic(self.server, self.rival, "Concurrente", 6, 6)
        self.server.start()
        self.url = reverse("decisions:board", args=[self.clinic.pk])
        self.client.force_login(self.player)

    def post(self, **data):
        return self.client.post(self.url, data, follow=True)

    def pending(self, kind=None):
        qs = self.clinic.decisions.filter(status=Status.PENDING)
        return qs.filter(kind=kind) if kind else qs

    def services_data(self, **overrides):
        data = {"action": "services"}
        for cs in self.clinic.services.select_related("service_type"):
            code = cs.service_type.code
            data[f"price_{code}"] = str(cs.price)
            if cs.active:
                data[f"active_{code}"] = "on"
        data.update(overrides)
        return data

    def products_data(self, **overrides):
        data = {"action": "products"}
        for item in self.clinic.stock.select_related("product_line"):
            code = item.product_line.code
            data[f"price_{code}"] = str(item.retail_price)
            data[f"target_{code}"] = str(item.target_quantity)
        data.update(overrides)
        return data

    # --- Accès ---------------------------------------------------------------

    def test_anonymous_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertRedirects(response, f"{reverse('login')}?next={self.url}")

    def test_rival_cannot_see_board_or_dashboard(self):
        self.client.force_login(self.rival)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.get(reverse("game:clinic_dashboard", args=[self.clinic.pk])).status_code, 404)
        self.assertEqual(self.client.post(self.url, self.services_data()).status_code, 404)

    def test_owner_sees_board(self):
        response = self.client.get(self.url)
        self.assertContains(response, "Tarifs et offre de soins")
        self.assertContains(response, "nécessite : Analyseur de biochimie")

    # --- Prestations -------------------------------------------------------------

    def test_unchanged_services_create_nothing(self):
        self.post(**self.services_data())
        self.assertFalse(self.pending().exists())

    def test_price_change_creates_one_decision_and_replaces_previous(self):
        self.post(**self.services_data(price_consultation="50"))
        self.post(**self.services_data(price_consultation="55"))
        decisions = self.pending(Kind.SET_SERVICE)
        self.assertEqual(decisions.count(), 1)
        self.assertEqual(Decimal(decisions.get().payload["price"]), Decimal("55"))
        self.assertTrue(self.clinic.decisions.filter(status=Status.CANCELLED).exists())
        # Le formulaire réaffiché montre la valeur planifiée.
        self.assertContains(self.client.get(self.url), 'value="55')

    def test_back_to_current_value_cancels_pending(self):
        self.post(**self.services_data(price_consultation="50"))
        self.post(**self.services_data())
        self.assertFalse(self.pending().exists())

    def test_service_requiring_equipment_cannot_be_activated(self):
        self.post(**self.services_data(active_biochimie="on"))
        self.assertFalse(self.pending().exists())

    def test_invalid_price_shows_errors(self):
        response = self.post(**self.services_data(price_consultation="-3"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "errorlist")
        self.assertFalse(self.pending().exists())

    # --- Produits ------------------------------------------------------------------

    def test_product_change(self):
        self.post(**self.products_data(**{"target_croq-chat-premium": "120", "price_croq-chat-premium": "30"}))
        decision = self.pending(Kind.SET_PRODUCT).get()
        self.assertEqual(decision.payload["target_quantity"], 120)

    # --- Personnel -----------------------------------------------------------------

    def test_hire(self):
        self.post(action="hire", role="vet", level="3", name="Dr Martin", gross_monthly_salary="4100")
        self.assertEqual(self.pending(Kind.HIRE).get().payload["name"], "Dr Martin")

    def test_hire_below_minimum_rejected(self):
        response = self.post(action="hire", role="asv", level="5", name="X", gross_monthly_salary="1000")
        self.assertContains(response, "errorlist")
        self.assertFalse(self.pending().exists())

    def test_dismiss_once(self):
        asv = self.clinic.staff.get(role="asv")
        self.post(action="dismiss", object_id=asv.pk)
        self.post(action="dismiss", object_id=asv.pk)
        self.assertEqual(self.pending(Kind.DISMISS).count(), 1)

    def test_cannot_dismiss_rival_staff(self):
        rival_vet = self.rival_clinic.staff.first()
        self.post(action="dismiss", object_id=rival_vet.pk)
        self.assertFalse(self.pending().exists())

    # --- Matériel ------------------------------------------------------------------

    def test_acquire_switching_mode_replaces(self):
        self.post(action="acquire", equipment="echographe", mode="purchase")
        self.post(action="acquire", equipment="echographe", mode="lease")
        self.assertEqual(self.pending(Kind.ACQUIRE_EQUIPMENT).get().payload["mode"], "lease")

    # --- Annulation et cycle complet ------------------------------------------------

    def test_cancel(self):
        self.post(action="hire", role="asv", level="5", name="Léa", gross_monthly_salary="2200")
        decision = self.pending().get()
        self.post(action="cancel", object_id=decision.pk)
        decision.refresh_from_db()
        self.assertEqual(decision.status, Status.CANCELLED)

    def test_full_cycle_with_resolution(self):
        self.post(action="acquire", equipment="lab-biochimie", mode="lease")
        self.post(action="hire", role="vet", level="3", name="Dr Martin", gross_monthly_salary="4100")
        self.post(**self.services_data(price_consultation="48"))
        resolve_quarter(self.server)
        self.assertFalse(self.pending().exists())
        self.assertEqual(self.clinic.decisions.filter(status=Status.APPLIED).count(), 3)
        self.assertTrue(self.clinic.services.get(service_type__code="biochimie").active)
        self.assertEqual(self.clinic.services.get(service_type__code="consultation").price, Decimal("48"))
        # Trimestre suivant : l'écran cible T2, l'historique montre les décisions appliquées.
        response = self.client.get(self.url)
        self.assertContains(response, "Trimestre 2/")
        self.assertContains(response, "Appliquée")

    # --- Format ---------------------------------------------------------------------

    def test_board_shows_positioning_and_financing(self):
        response = self.client.get(self.url)
        self.assertContains(response, "Positionnement de la clinique")
        self.assertContains(response, "Centre hospitalier vétérinaire (CHV)")
        self.assertContains(response, "✗ Au moins 6 vétérinaires à temps plein (actuellement 1)")
        self.assertContains(response, "✗ Au moins 6 vétérinaires spécialistes (échelon 5)")
        self.assertContains(response, "✗ Au moins 6 ASV titrées (échelon 5) à temps plein (actuellement 1)")
        self.assertContains(response, "Financement")
        self.assertContains(response, "Capacité d'emprunt restante")

    def test_chv_checklist_reflects_pending_decisions(self):
        for specialty in ("chirurgie", "chirurgie", "chirurgie", "imagerie", "imagerie", "imagerie"):
            self.post(action="hire", role="vet", level="5", specialty=specialty, name="Spé", gross_monthly_salary="6000")
        self.post(action="hire", role="asv", level="3", name="AVQ", gross_monthly_salary="2000")  # ne compte pas
        self.post(action="hire", role="asv", level="5", name="ASV", gross_monthly_salary="2200")
        self.post(action="acquire", equipment="echographe", mode="lease")
        response = self.client.get(self.url)
        self.assertContains(response, "✓ Au moins 6 vétérinaires à temps plein (actuellement 7)")
        self.assertContains(
            response,
            "✓ Au moins 6 vétérinaires spécialistes (échelon 5) dans 2 disciplines "
            "(actuellement 6 : chirurgie, imagerie médicale)",
        )
        self.assertContains(response, "✗ Au moins 6 ASV titrées (échelon 5) à temps plein (actuellement 2)")
        self.assertContains(response, "✗ Au moins 3 techniques d&#x27;imagerie (actuellement 1 : Échographe)")
        self.assertContains(response, "✗ Analyseur de biochimie")

    def test_set_format_replaces_and_cancels(self):
        self.post(action="format", format="low_cost")
        self.post(action="format", format="chv")
        self.assertEqual(self.pending(Kind.SET_FORMAT).get().payload["format"], "chv")
        self.post(action="format", format="standard")  # retour au format actuel
        self.assertFalse(self.pending(Kind.SET_FORMAT).exists())

    def test_chv_switch_shows_fitout_cost(self):
        self.post(action="format", format="chv")
        self.assertContains(self.client.get(self.url), "-60000 €")

    def test_low_cost_services_marked_unavailable(self):
        self.post(action="format", format="low_cost")
        resolve_quarter(self.server)
        response = self.client.get(self.url)
        self.assertContains(response, "hors offre low-cost")

    # --- Emprunts --------------------------------------------------------------------

    def test_borrow_within_capacity(self):
        self.post(action="borrow", amount="100000", duration_quarters="20")
        decision = self.pending(Kind.BORROW).get()
        self.assertEqual(decision.payload, {"amount": "100000", "duration_quarters": 20})
        self.assertContains(self.client.get(self.url), "100000 €")

    def test_borrow_beyond_capacity_refused_immediately(self):
        response = self.post(action="borrow", amount="300000", duration_quarters="20")
        self.assertContains(response, "La banque refuserait ce prêt")
        self.assertFalse(self.pending().exists())

    def test_pending_borrowing_reduces_capacity(self):
        self.post(action="borrow", amount="200000", duration_quarters="20")
        response = self.post(action="borrow", amount="100000", duration_quarters="20")
        self.assertContains(response, "La banque refuserait ce prêt")
        self.assertEqual(self.pending(Kind.BORROW).count(), 1)

    def test_repay_loan(self):
        self.post(action="borrow", amount="50000", duration_quarters="20")
        resolve_quarter(self.server)
        loan = self.clinic.loans.get()
        response = self.client.get(self.url)
        self.assertContains(response, "1500 € d'indemnité")
        self.post(action="repay", object_id=loan.pk)
        self.assertEqual(self.pending(Kind.REPAY_LOAN).get().payload["loan_id"], loan.pk)

    def test_cannot_repay_rival_loan(self):
        from accounting.loans import borrow

        loan = borrow(self.rival_clinic, Decimal("20000"), 8, self.server.get_parameters())
        self.post(action="repay", object_id=loan.pk)
        self.assertFalse(self.pending().exists())

    def test_finished_server_is_read_only(self):
        self.server.status = GameServer.Status.FINISHED
        self.server.save()
        response = self.post(action="hire", role="vet", level="3", name="Dr X", gross_monthly_salary="4000")
        self.assertContains(response, "lecture seule")
        self.assertFalse(self.pending().exists())
