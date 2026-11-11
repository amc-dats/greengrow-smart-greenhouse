from django.urls import path

from . import views

urlpatterns = [
    path("health/", views.HealthView.as_view()),
    path("status/", views.StatusView.as_view()),
    # sense
    path("readings/", views.ReadingList.as_view()),
    path("readings/latest/", views.ReadingLatest.as_view()),
    path("weather/", views.WeatherList.as_view()),
    # act
    path("actuators/", views.ActuatorList.as_view()),
    path("actuators/<str:device>/command/", views.ActuatorCommand.as_view()),
    path("feed/", views.Feed.as_view()),
    path("commands/", views.CommandList.as_view()),
    path("commands/<int:pk>/", views.CommandDetail.as_view()),
    # generator
    path("sim/step/", views.SimStep.as_view()),
    path("sim/state/", views.SimState.as_view()),
    path("sim/fault/", views.SimFault.as_view()),
    # assist
    path("assist/explain/", views.Explain.as_view()),
    path("assist/suggestions/", views.SuggestionList.as_view()),
    path("assist/suggestions/<int:pk>/", views.SuggestionDetail.as_view()),
    path("assist/suggestions/<int:pk>/approve/", views.SuggestionApprove.as_view()),
    path("assist/suggestions/<int:pk>/reject/", views.SuggestionReject.as_view()),
]
