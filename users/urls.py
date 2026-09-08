from django.urls import path
from . import views

app_name = "users"
urlpatterns = [path("", views.index, name="index"), path("profile/", views.profile, name="profile"), path("add/", views.create, name="create"), path("<int:pk>/", views.edit, name="edit")]
