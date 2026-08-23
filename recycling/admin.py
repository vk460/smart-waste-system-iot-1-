from django.contrib import admin
from .models import Machine, MachineEvent, Notification, Profile, RFIDCard, Redemption, RecyclingSession, Reward, RewardRule

admin.site.register([Profile, RFIDCard, Machine, RecyclingSession, Reward, RewardRule, Redemption, Notification, MachineEvent])
