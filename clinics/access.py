"""Contrôle d'accès aux cliniques.

Un joueur ne voit et ne pilote que sa propre clinique (ses comptes et ses
décisions en attente sont confidentiels vis-à-vis des concurrents).
Les membres de l'équipe pédagogique (``is_staff``) ont accès à tout.
"""

from django.http import Http404

from .models import Clinic


def can_access(user, clinic: Clinic) -> bool:
    return user.is_authenticated and (user.is_staff or clinic.owner_id == user.pk)


def get_clinic_for_user(user, pk: int) -> Clinic:
    clinic = Clinic.objects.select_related("server", "owner").filter(pk=pk).first()
    # 404 plutôt que 403 : on ne révèle pas l'existence des cliniques concurrentes.
    if clinic is None or not can_access(user, clinic):
        raise Http404("Clinique introuvable.")
    return clinic
