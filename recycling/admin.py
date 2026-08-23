from django.contrib import admin
from .models import Machine, MachineEvent, Notification, Profile, RFIDCard, Redemption, RecyclingPointConfig, RecyclingSession, Reward, RewardRule

admin.site.register([Profile, RFIDCard, Machine, RecyclingSession, Reward, RewardRule, RecyclingPointConfig, Redemption, Notification, MachineEvent])
