from django.urls import path

from . import views


app_name = "catalog"

urlpatterns = [
    path("", views.index, name="index"),
    path("new/", views.create, name="create"),
    path("<int:pk>/edit/", views.edit, name="edit"),
]
