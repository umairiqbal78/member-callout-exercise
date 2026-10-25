from django.urls import path

from .views import (
    AnnouncementAIDraftView,
    AnnouncementApproveView,
    AnnouncementDetailView,
    AnnouncementListCreateView,
    AnnouncementSendView,
    AnnouncementStatsView,
    InboxView,
    LoginView,
    MeView,
    ReceiptBatchView,
    RespondView,
)

app_name = "callouts"

urlpatterns = [
    # Auth
    path("auth/login/", LoginView.as_view(), name="login"),
    # Me
    path("me/", MeView.as_view(), name="me"),
    path("me/inbox/", InboxView.as_view(), name="inbox"),
    path("me/receipts/", ReceiptBatchView.as_view(), name="receipts"),
    path(
        "me/announcements/<uuid:pk>/respond/",
        RespondView.as_view(),
        name="respond",
    ),
    # Leader
    path("announcements/", AnnouncementListCreateView.as_view(), name="announcement-list"),
    path(
        "announcements/ai-draft/",
        AnnouncementAIDraftView.as_view(),
        name="announcement-ai-draft",
    ),
    path(
        "announcements/<uuid:pk>/",
        AnnouncementDetailView.as_view(),
        name="announcement-detail",
    ),
    path(
        "announcements/<uuid:pk>/approve/",
        AnnouncementApproveView.as_view(),
        name="announcement-approve",
    ),
    path(
        "announcements/<uuid:pk>/send/",
        AnnouncementSendView.as_view(),
        name="announcement-send",
    ),
    path(
        "announcements/<uuid:pk>/stats/",
        AnnouncementStatsView.as_view(),
        name="announcement-stats",
    ),
]
