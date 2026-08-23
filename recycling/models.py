from django.contrib.auth.models import User
from django.db import models
from django.db.models import Q
from django.utils import timezone
import uuid


def make_session_id():
    return f"SES_{timezone.now():%Y%m%d}_{uuid.uuid4().hex[:8].upper()}"


def make_registration_id():
    return f"RFID_{timezone.now():%Y%m%d}_{uuid.uuid4().hex[:8].upper()}"


def make_transaction_id():
    return f"TXN-{uuid.uuid4().hex[:8].upper()}"


class Profile(models.Model):
    ROLE_CHOICES = [("USER", "User"), ("ADMIN", "Admin")]
    STATUS_CHOICES = [("ACTIVE", "Active"), ("BLOCKED", "Blocked")]
    RFID_STATUS_CHOICES = [("ACTIVE", "Active"), ("BLOCKED", "Blocked")]
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default="USER")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="ACTIVE")
    rfid_uid = models.CharField(max_length=64, blank=True, unique=True, null=True)
    rfid_status = models.CharField(max_length=10, choices=RFID_STATUS_CHOICES, default="ACTIVE")
    phone = models.CharField(max_length=30, blank=True)
    points = models.PositiveIntegerField(default=0)
    monthly_goal_kg = models.DecimalField(max_digits=6, decimal_places=2, default=5)

class Machine(models.Model):
    STATUS_CHOICES = [("ONLINE", "Online"), ("OFFLINE", "Offline"), ("MAINTENANCE", "Maintenance")]
    code = models.CharField(max_length=40, unique=True)
    location = models.CharField(max_length=120, default="Main Campus")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="ONLINE")
    bin_level = models.PositiveIntegerField(default=40)
    api_key = models.CharField(max_length=120, default="change-me")
    last_seen = models.DateTimeField(auto_now=True)

class RFIDCard(models.Model):
    uid = models.CharField(max_length=64, unique=True)
    card_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="rfid_cards")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.card_id or self.uid

class RecyclingSession(models.Model):
    STATUS_CHOICES = [(x, x.replace("_", " ").title()) for x in ["RFID_VERIFIED", "READY_FOR_DEPOSIT", "MEASURING", "PROCESSING", "COMPLETED", "REJECTED"]]
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    machine = models.ForeignKey(Machine, on_delete=models.PROTECT)
    rfid_card = models.ForeignKey(RFIDCard, on_delete=models.PROTECT, null=True, blank=True, related_name="sessions")
    session_id = models.CharField(max_length=32, editable=False, default=make_session_id)
    rfid_uid = models.CharField(max_length=64)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="RFID_VERIFIED")
    weight_grams = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    points = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

class Reward(models.Model):
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=40, default="Eco Products")
    image_url = models.URLField(blank=True)
    image = models.FileField(upload_to="reward_images/", blank=True)
    points_required = models.PositiveIntegerField()
    stock = models.PositiveIntegerField(default=10)
    active = models.BooleanField(default=True)


class RewardRule(models.Model):
    name = models.CharField(max_length=120, default="Standard paper reward")
    minimum_grams = models.DecimalField(max_digits=8, decimal_places=2)
    maximum_grams = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    points = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)


class RecyclingPointConfig(models.Model):
    config_key = models.PositiveSmallIntegerField(default=1)
    grams_per_point = models.DecimalField(max_digits=8, decimal_places=2, default=1)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["config_key"], condition=Q(active=True), name="one_active_recycling_point_config"),
        ]

    def __str__(self):
        return f"{self.grams_per_point} grams per point"


class Redemption(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    reward = models.ForeignKey(Reward, on_delete=models.PROTECT)
    points_used = models.PositiveIntegerField()
    coupon_code = models.CharField(max_length=40, unique=True)
    transaction_id = models.CharField(max_length=24, unique=True, default=make_transaction_id)
    status = models.CharField(max_length=20, default="REDEEMED")
    redeemed_at = models.DateTimeField(auto_now_add=True)


class MachineEvent(models.Model):
    machine = models.ForeignKey(Machine, on_delete=models.CASCADE)
    event_type = models.CharField(max_length=60)
    data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class RFIDRegistration(models.Model):
    STATUS_CHOICES = [("WAITING", "Waiting"), ("DETECTED", "Detected"), ("LINKED", "Linked"), ("CANCELLED", "Cancelled")]
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE)
    machine = models.ForeignKey(Machine, on_delete=models.PROTECT)
    registration_id = models.CharField(max_length=32, unique=True, default=make_registration_id)
    rfid_uid = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="WAITING")
    created_at = models.DateTimeField(auto_now_add=True)
    linked_at = models.DateTimeField(null=True, blank=True)

class Notification(models.Model):
    TYPE_CHOICES = [(value, value.title()) for value in ["RECYCLING", "REWARD", "ACHIEVEMENT", "MACHINE", "GOAL", "REDEMPTION"]]
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES, default="RECYCLING")
    title = models.CharField(max_length=140)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
