from django.urls import path

from .views import (
    ForgotPasswordView,
    LoginView,
    OneTapWipeView,
    SafetyPreferencesView,
    SafetyReportView,
    SignupView,
    ZKLivenessVerifyView,
)

urlpatterns = [
    path('signup/', SignupView.as_view(), name='signup'),
    path('login/', LoginView.as_view(), name='login'),
    path('forgot_password/', ForgotPasswordView.as_view(), name='forgot_password'),
    path('zk-verify-liveness/', ZKLivenessVerifyView.as_view(), name='zk_verify_liveness'),
    path('safety-preferences/', SafetyPreferencesView.as_view(), name='safety_preferences'),
    path('report/', SafetyReportView.as_view(), name='safety_report'),
    path('one-tap-wipe/', OneTapWipeView.as_view(), name='one_tap_wipe'),
]
