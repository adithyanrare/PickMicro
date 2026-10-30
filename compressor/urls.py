from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("compress/", views.compress, name="compress"),
    path("history/", views.history, name="history"),
    path("health/", views.health, name="health"),
    path("download/<str:record_id>/", views.download, name="download"),
]
