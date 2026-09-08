from django.urls import path
from . import views

app_name = "photos"
urlpatterns = [
    path("upload/<int:tour_pk>/", views.upload, name="upload"),
    path("remove/<int:photo_pk>/", views.remove, name="remove"),
    path("<uuid:token>/", views.album, name="album"),
    path("<uuid:token>/<int:photo_pk>/<str:variant>/", views.asset, name="asset"),
]
