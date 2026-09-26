from django.urls import path

from mutint_fastqc import views

urlpatterns = [
    path('report/<int:pk>', views.report, name='fastqc_report'),
]
