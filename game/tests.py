from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from engine.resolution import resolve_quarter
from game.models import GameServer


class ViewsSmokeTests(TestCase):
    def setUp(self):
        call_command("seed_demo", clinics=4, stdout=open("/dev/null", "w"))
        self.server = GameServer.objects.get()
        resolve_quarter(self.server)
        self.user = get_user_model().objects.create_user("prof", is_staff=True)
        self.client.force_login(self.user)

    def test_pages_render(self):
        clinic = self.server.clinics.first()
        for url in (
            reverse("game:server_list"),
            reverse("game:server_map", args=[self.server.pk]),
            reverse("game:clinic_dashboard", args=[clinic.pk]),
        ):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_demo_has_clinics_and_zones(self):
        self.assertEqual(self.server.clinics.count(), 4)
        self.assertEqual(self.server.zones.count(), self.server.grid_width * self.server.grid_height)
