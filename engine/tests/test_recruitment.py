"""Difficulté de recrutement : chances, vivier limité, concurrence, frais."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounting import pcg
from accounting.services import account_balance
from clinics import recruitment
from clinics.services import create_clinic
from decisions.models import PendingDecision
from engine.parameters import SimulationParameters
from engine.resolution import resolve_quarter
from game.catalog import seed_catalog
from game.map import generate_zones
from game.models import GameServer
from game.placement import cell_insights

Kind = PendingDecision.Kind
Status = PendingDecision.Status


class RecruitmentCase(TestCase):
    def setUp(self):
        seed_catalog()
        self.server = GameServer.objects.create(name="Emploi", grid_width=10, grid_height=10, seed=8)
        generate_zones(self.server, towns=1)
        insights = cell_insights(self.server)
        self.dense = max(insights.values(), key=lambda i: i.catchment)
        self.rural = min(insights.values(), key=lambda i: i.catchment)
        User = get_user_model()
        self.city = create_clinic(self.server, User.objects.create(username="ville"), "Ville", self.dense.x, self.dense.y)
        self.country = create_clinic(
            self.server, User.objects.create(username="campagne"), "Campagne", self.rural.x, self.rural.y
        )
        self.server.start()
        self.params = self.server.get_parameters()

    def hire(self, clinic, role="vet", level=3, salary="4000", specialty="", name="Candidat"):
        payload = {"role": role, "level": level, "name": name, "gross_monthly_salary": salary}
        if specialty:
            payload["specialty"] = specialty
        return PendingDecision.objects.create(clinic=clinic, quarter=self.server.current_quarter, kind=Kind.HIRE, payload=payload)


class ChanceTests(RecruitmentCase):
    def chance(self, clinic=None, salary="3800", params=None, **kw):
        kw.setdefault("role", "vet")
        kw.setdefault("level", 3)
        kw.setdefault("specialty", "")
        return recruitment.chance(clinic or self.city, salary=Decimal(salary), params=params or self.params, **kw)

    def test_market_salary_above_minimum(self):
        # Échelon 3 : 3 268,72 € x 1,15 ; spécialiste : 4 310,40 € x 1,45.
        self.assertEqual(recruitment.market_salary("vet", 3, "", self.params), Decimal("3759"))
        self.assertEqual(recruitment.market_salary("vet", 5, "chirurgie", self.params), Decimal("6250"))

    def test_salary_raises_chances_up_to_a_cap(self):
        low, market, high, huge = (self.chance(salary=s) for s in ("3300", "3759", "4200", "9000"))
        self.assertLess(low, market)
        self.assertLess(market, high)
        self.assertLessEqual(huge, 0.98)
        self.assertAlmostEqual(market, self.params.recruitment_base_vet, delta=0.01)  # zone la plus dense, réputation 0,5

    def test_rural_clinics_recruit_with_more_difficulty(self):
        self.assertLess(self.chance(clinic=self.country), self.chance(clinic=self.city))

    def test_reputation_helps(self):
        self.city.reputation = 0.9
        better = self.chance()
        self.city.reputation = 0.2
        self.assertGreater(better, self.chance())

    def test_specialists_are_scarcer_and_chv_attracts_them(self):
        specialist = self.chance(role="vet", level=5, specialty="chirurgie", salary="6250")
        self.assertLess(specialist, self.chance(salary="3759"))
        self.city.format = "chv"
        self.assertGreater(self.chance(role="vet", level=5, specialty="chirurgie", salary="6250"), specialist)
        self.assertLess(self.chance(salary="3759"), self.params.recruitment_base_vet)  # gardes de nuit

    def test_shortage_and_disabled(self):
        shortage = SimulationParameters(recruitment_shortage=0.5)
        self.assertAlmostEqual(self.chance(params=shortage, salary="3759"), self.chance(salary="3759") / 2, delta=0.01)
        self.assertEqual(self.chance(params=SimulationParameters(recruitment_enabled=0)), 1.0)


class MarketTests(RecruitmentCase):
    def test_fees_are_due_even_when_hiring_fails(self):
        self.server.parameters = {"recruitment_base_asv": 0.0}  # aucun candidat
        self.server.save()
        decision = self.hire(self.city, role="asv", level=5, salary="2300")
        resolve_quarter(self.server)
        decision.refresh_from_db()
        self.assertEqual(decision.status, Status.REJECTED)
        self.assertIn("aucun candidat", decision.message)
        self.assertEqual(account_balance(self.city, pcg.RECRUITMENT), Decimal("500"))
        self.assertEqual(self.city.staff.filter(role="asv").count(), 1)  # l'ASV de départ seulement

    def test_successful_hire(self):
        self.server.parameters = {"recruitment_base_vet": 1.0}
        self.server.save()
        decision = self.hire(self.city, salary="4500")
        report = {r.clinic.name: r for r in resolve_quarter(self.server)}["Ville"]
        decision.refresh_from_db()
        self.assertEqual(decision.status, Status.APPLIED)
        self.assertEqual(self.city.staff.filter(role="vet").count(), 2)
        self.assertEqual(report.details["recruitment"][0]["result"], "hired")
        self.assertEqual(account_balance(self.city, pcg.RECRUITMENT), Decimal("1500"))

    def test_limited_pool_creates_competition(self):
        """Un seul chirurgien spécialiste disponible : deux cliniques le veulent, une seule l'obtient."""
        self.server.parameters = {"recruitment_base_specialist": 1.0}
        self.server.save()
        first = self.hire(self.city, level=5, specialty="chirurgie", salary="7000")
        second = self.hire(self.country, level=5, specialty="chirurgie", salary="7000")
        resolve_quarter(self.server)
        statuses = sorted(PendingDecision.objects.get(pk=d.pk).status for d in (first, second))
        self.assertEqual(statuses, [Status.APPLIED, Status.REJECTED])
        loser = PendingDecision.objects.get(pk__in=[first.pk, second.pk], status=Status.REJECTED)
        self.assertIn("offres plus attractives", loser.message)

    def test_pools_are_per_specialty(self):
        self.server.parameters = {"recruitment_base_specialist": 1.0}
        self.server.save()
        self.hire(self.city, level=5, specialty="chirurgie", salary="7000")
        self.hire(self.country, level=5, specialty="imagerie", salary="7000")
        resolve_quarter(self.server)
        self.assertEqual(PendingDecision.objects.filter(status=Status.APPLIED, kind=Kind.HIRE).count(), 2)

    def test_invalid_offer_costs_nothing(self):
        decision = self.hire(self.city, salary="2000")  # sous le minimum conventionnel
        resolve_quarter(self.server)
        decision.refresh_from_db()
        self.assertEqual(decision.status, Status.REJECTED)
        self.assertIn("minimum conventionnel", decision.message)
        self.assertEqual(account_balance(self.city, pcg.RECRUITMENT), Decimal("0"))

    def test_deterministic(self):
        for i in range(6):
            self.hire(self.city, role="asv", level=5, salary="2200", name=f"ASV {i}")
        resolve_quarter(self.server)
        first = list(PendingDecision.objects.order_by("id").values_list("status", flat=True))

        twin = GameServer.objects.create(name="Jumeau", grid_width=10, grid_height=10, seed=8)
        generate_zones(twin, towns=1)
        clinic = create_clinic(twin, get_user_model().objects.create(username="j"), "Ville", self.dense.x, self.dense.y)
        create_clinic(twin, get_user_model().objects.create(username="k"), "Campagne", self.rural.x, self.rural.y)
        twin.start()
        decisions = [
            PendingDecision.objects.create(
                clinic=clinic, quarter=1, kind=Kind.HIRE,
                payload={"role": "asv", "level": 5, "name": f"ASV {i}", "gross_monthly_salary": "2200"},
            )
            for i in range(6)
        ]
        resolve_quarter(twin)
        self.assertEqual([PendingDecision.objects.get(pk=d.pk).status for d in decisions], first)


class BoardTests(RecruitmentCase):
    def test_board_shows_market_and_odds(self):
        self.client.force_login(self.city.owner)
        response = self.client.get(reverse("decisions:board", args=[self.city.pk]))
        self.assertContains(response, "Marché de l'emploi")
        self.assertContains(response, 'id="recruitment-data"')
        self.hire(self.city, salary="3800")
        self.assertContains(self.client.get(reverse("decisions:board", args=[self.city.pk])), "chances estimées")
