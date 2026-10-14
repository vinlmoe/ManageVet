import hmac

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm


class SignupForm(UserCreationForm):
    first_name = forms.CharField(label="Prénom", max_length=150)
    last_name = forms.CharField(label="Nom", max_length=150)
    email = forms.EmailField(label="Adresse électronique")

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ("username", "first_name", "last_name", "email")

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if get_user_model().objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Un compte existe déjà avec cette adresse.")
        return email


class JoinServerForm(forms.Form):
    """Création de sa clinique sur un serveur : nom, case choisie, code d'accès."""

    name = forms.CharField(label="Nom de votre clinique", max_length=100)
    cell = forms.RegexField(
        regex=r"^\d{1,3},\d{1,3}$",
        error_messages={"required": "Choisissez une case libre sur la carte.", "invalid": "Case invalide."},
    )
    join_code = forms.CharField(label="Code d'accès", max_length=32, required=False)

    def __init__(self, *args, server, **kwargs):
        super().__init__(*args, **kwargs)
        self.server = server
        if not server.join_code:
            del self.fields["join_code"]

    def clean_cell(self):
        x, y = (int(v) for v in self.cleaned_data["cell"].split(","))
        if not (0 <= x < self.server.grid_width and 0 <= y < self.server.grid_height):
            raise forms.ValidationError("Case hors de la carte.")
        if self.server.clinics.filter(x=x, y=y).exists():
            raise forms.ValidationError("Cette case est déjà occupée par une clinique.")
        return x, y

    def clean_join_code(self):
        code = self.cleaned_data.get("join_code", "").strip()
        if not hmac.compare_digest(code.encode(), self.server.join_code.encode()):
            raise forms.ValidationError("Code d'accès incorrect.")
        return code

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if self.server.clinics.filter(name__iexact=name).exists():
            raise forms.ValidationError("Ce nom est déjà pris sur cette partie.")
        return name
