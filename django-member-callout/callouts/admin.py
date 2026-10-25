from django.contrib import admin

from .models import (
    Announcement,
    AnnouncementRecipient,
    LeadershipRole,
    Local,
    Member,
    MemberDevice,
)

admin.site.register(Local)
admin.site.register(Member)
admin.site.register(LeadershipRole)
admin.site.register(MemberDevice)
admin.site.register(Announcement)
admin.site.register(AnnouncementRecipient)
