from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from clinics.models import Clinic
from clinics.services import create_clinic
from game.catalog import seed_catalog
from game.map import generate_zones
from game.models import GameServer
from game.placement import BASE_RENT, MAX_URBAN_SURCHARGE, cell_insights

User = get_user_model()


class SignupTests(TestCase):
    def signup_data(self, **overrides):
        data = {
            "username": "camille",
            "first_name": "Camille",
            "last_name": "Durand",
            "email": "camille@example.org",
            "password1": "Vetobrain-2026!",
            "password2": "Vetobrain-2026!",
        }
        data.update(overrides)
        return data

    def test_signup_creates_user_and_logs_in(self):
        response = self.client.post(reverse("players:signup"), self.signup_data())
        self.assertRedirects(response, reverse("game:server_list"))
        user = User.objects.get(username="camille")
        self.assertFalse(user.is_staff)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_duplicate_email_rejected(self):
        User.objects.create_user("autre", email="camille@example.org")
        response = self.client.post(reverse("players:signup"), self.signup_data(email="Camille@Example.org"))
        self.assertContains(response, "existe déjà")
        self.assertFalse(User.objects.filter(username="camille").exists())

    def test_weak_password_rejected(self):
        response = self.client.post(reverse("players:signup"), self.signup_data(password1="1234", password2="1234"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="camille").exists())

    @override_settings(SIGNUP_ENABLED=False)
    def test_signup_can_be_disabled(self):
        self.assertEqual(self.client.get(reverse("players:signup")).status_code, 404)


class JoinServerTests(TestCase):
    def setUp(self):
        seed_catalog()
        self.server = GameServer.objects.create(name="Promo 2026", grid_width=10, grid_height=10, seed=5, max_clinics=3)
        generate_zones(self.server, towns=1)
        self.rival = create_clinic(self.server, User.objects.create_user("rival"), "Rivale", 1, 1)
        self.student = User.objects.create_user("etudiant")
        self.client.force_login(self.student)
        self.url = reverse("players:join", args=[self.server.pk])

    def join(self, **data):
        payload = {"name": "Clinique du Parc", "cell": "5,5"}
        payload.update(data)
        return self.client.post(self.url, payload)

    def test_page_shows_map_and_free_cells(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'name="cell" value="5,5"')
        self.assertNotContains(response, 'name="cell" value="1,1"')  # case occupée

    def test_numbers_in_html_attributes_are_not_localized(self):
        # En fr-FR, Django écrirait « 0,45 » : CSS et JavaScript ne le comprennent pas.
        html = self.client.get(self.url).content.decode()
        self.assertRegex(html, r"rgba\(39, 174, 96, 0\.\d\d\)")
        self.assertNotRegex(html, r"rgba\(39, 174, 96, \d,")
        self.assertRegex(html, r'data-nearest="\d+\.\d"')

    def test_join_creates_clinic_with_location_rent(self):
        response = self.join()
        clinic = Clinic.objects.get(owner=self.student)
        self.assertRedirects(response, reverse("decisions:board", args=[clinic.pk]))
        self.assertEqual((clinic.x, clinic.y), (5, 5))
        self.assertEqual(clinic.cash, Decimal("80000"))
        self.assertEqual(clinic.rent_per_quarter, cell_insights(self.server)[(5, 5)].rent)
        self.assertEqual(clinic.staff.count(), 2)

    def test_cannot_join_twice(self):
        self.join()
        response = self.join(name="Deuxième", cell="7,7")
        self.assertEqual(Clinic.objects.filter(owner=self.student).count(), 1)
        self.assertRedirects(response, reverse("game:clinic_dashboard", args=[Clinic.objects.get(owner=self.student).pk]))

    def test_occupied_cell_rejected(self):
        response = self.join(cell="1,1")
        self.assertContains(response, "déjà occupée")
        self.assertFalse(Clinic.objects.filter(owner=self.student).exists())

    def test_out_of_map_cell_rejected(self):
        response = self.join(cell="10,2")
        self.assertContains(response, "hors de la carte")

    def test_duplicate_name_rejected(self):
        response = self.join(name="rivale")
        self.assertContains(response, "déjà pris")

    def test_join_code_required_when_set(self):
        self.server.join_code = "VETO26"
        self.server.save()
        self.assertContains(self.join(join_code="mauvais"), "Code d&#x27;accès incorrect")
        self.assertFalse(Clinic.objects.filter(owner=self.student).exists())
        self.join(join_code="VETO26")
        self.assertTrue(Clinic.objects.filter(owner=self.student).exists())

    def test_full_or_closed_server_refuses(self):
        for flag in ("full", "closed", "finished"):
            server = GameServer.objects.create(name=flag, grid_width=5, grid_height=5, max_clinics=1 if flag == "full" else 5)
            generate_zones(server, towns=1)
            if flag == "full":
                create_clinic(server, User.objects.create_user(f"occupant-{flag}"), "Pleine", 0, 0)
            elif flag == "closed":
                server.registration_open = False
            else:
                server.status = GameServer.Status.FINISHED
            server.save()
            response = self.client.post(reverse("players:join", args=[server.pk]), {"name": "X", "cell": "3,3"})
            self.assertRedirects(response, reverse("game:server_list"), msg_prefix=flag)
        self.assertFalse(Clinic.objects.filter(owner=self.student).exists())

    def test_server_list_offers_join(self):
        self.assertContains(self.client.get(reverse("game:server_list")), self.url)


class PlacementTests(TestCase):
    def setUp(self):
        self.server = GameServer.objects.create(name="Carte", grid_width=12, grid_height=12, seed=9)
        generate_zones(self.server, towns=1)

    def test_rent_is_higher_where_population_is_dense(self):
        insights = cell_insights(self.server)
        densest = max(insights.values(), key=lambda i: i.catchment)
        emptiest = min(insights.values(), key=lambda i: i.catchment)
        self.assertGreater(densest.rent, emptiest.rent)
        self.assertEqual(densest.rent, BASE_RENT + MAX_URBAN_SURCHARGE)
        self.assertGreaterEqual(emptiest.rent, BASE_RENT)

    def test_competition_metrics(self):
        seed_catalog()
        create_clinic(self.server, User.objects.create_user("a"), "A", 0, 0)
        insights = cell_insights(self.server)
        self.assertEqual(insights[(0, 0)].nearest_competitor_km, 0.0)
        self.assertEqual(insights[(3, 4)].nearest_competitor_km, 10.0)  # 5 cases x 2 km
        self.assertEqual(insights[(11, 11)].competitors_nearby, 0)
        self.assertEqual(insights[(1, 1)].competitors_nearby, 1)
