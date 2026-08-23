import json
import logging
import re
import secrets
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, Sum
from django.db.models.functions import TruncDate
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import Machine, MachineEvent, Notification, Profile, RFIDCard, RFIDRegistration, Redemption, RecyclingPointConfig, RecyclingSession, Reward, RewardRule

REWARD_IMAGE_CHOICES = [
    ("/static/images/eco-bottle.webp", "Eco bottle"),
    ("/static/images/electric kettle.jpg", "Electric kettle"),
    ("/static/images/food steamer.png", "Food steamer"),
    ("/static/images/hot flask.webp", "Hot flask"),
    ("/static/images/llifestyle -mug.jpg", "Lifestyle mug"),
    ("/static/images/stainless steel bottle.png", "Stainless steel bottle"),
]

logger = logging.getLogger(__name__)


def json_body(request):
    try:
        return json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return None


def normalize_phone(phone):
    phone = re.sub(r"[\s-]", "", str(phone or ""))
    if phone.startswith("+91"):
        phone = phone[3:]
    return phone if re.fullmatch(r"[6-9]\d{9}", phone) else None


def calculate_points(weight_grams):
    weight = Decimal(str(weight_grams))
    config = RecyclingPointConfig.objects.filter(active=True).order_by("-updated_at", "-id").first()
    grams_per_point = config.grams_per_point if config else Decimal("1")
    if grams_per_point <= 0 or weight <= 0:
        return 0
    return int(weight // grams_per_point)


points_for_weight = calculate_points


def notify(user, title, message, notification_type="RECYCLING"):
    return Notification.objects.create(user=user, type=notification_type, title=title, message=message)


def user_payload(user):
    profile, _ = Profile.objects.get_or_create(user=user)
    card = RFIDCard.objects.filter(user=user, is_active=True).first()
    return {"id": user.id, "name": user.get_full_name() or user.username, "email": user.email, "phone": profile.phone, "rfid_uid": card.uid if card else profile.rfid_uid, "card_id": card.card_id if card else None, "rfid_status": profile.rfid_status, "points": profile.points, "status": profile.status}


def session_payload(session):
    card = session.rfid_card
    return {"id": session.id, "session_id": session.session_id, "user_id": session.user_id, "machine_id": session.machine_id, "machine_code": session.machine.code, "rfid_uid": card.uid if card else session.rfid_uid, "card_id": card.card_id if card else None, "status": session.status, "weight": float(session.weight_grams), "points": session.points, "started_at": session.created_at.isoformat(), "completed_at": session.completed_at.isoformat() if session.completed_at else None}


def dashboard(request):
    if not request.user.is_authenticated:
        completed = RecyclingSession.objects.filter(status="COMPLETED")
        total_grams = completed.aggregate(value=Sum("weight_grams"))["value"] or 0
        return render(request, "home.html", {
            "home_paper_kg": Decimal(total_grams) / 1000,
            "home_users": Profile.objects.filter(role="USER", status="ACTIVE").count(),
            "home_redemptions": Redemption.objects.count(),
            "home_trees": int(Decimal(total_grams) / 40000),
        })
    if hasattr(request.user, "profile") and request.user.profile.role == "ADMIN":
        return redirect("admin_dashboard")
    profile, _ = Profile.objects.get_or_create(user=request.user)
    sessions = RecyclingSession.objects.filter(user=request.user, status="COMPLETED")
    total_grams = sessions.aggregate(value=Sum("weight_grams"))["value"] or 0
    return render(request, "user/dashboard.html", {"profile": profile, "sessions": sessions.order_by("-created_at")[:4], "total_weight": Decimal(total_grams) / 1000, "deposits": sessions.count(), "co2_saved": Decimal(total_grams) * Decimal("1.15") / 1000, "notifications": Notification.objects.filter(user=request.user, is_read=False)[:5]})


@login_required
def recycle(request):
    return render(request, "user/recycle.html", {"machines": Machine.objects.filter(status="ONLINE"), "profile": request.user.profile})


@login_required
def mobile_dashboard(request):
    profile, _ = Profile.objects.get_or_create(user=request.user)
    return render(request, "mobile/dashboard.html", {"profile": profile})


@login_required
def mobile_recycle(request):
    return render(request, "mobile/recycle.html", {"machines": Machine.objects.filter(status="ONLINE"), "profile": request.user.profile})


@login_required
def session_progress(request, session_id):
    session = get_object_or_404(RecyclingSession, session_id=session_id, user=request.user)
    return render(request, "user/session_progress.html", {"session": session})


@login_required
def history(request):
    return render(request, "user/history.html", {"sessions": RecyclingSession.objects.filter(user=request.user).select_related("machine").order_by("-created_at")})


@login_required
def rewards(request):
    products = Reward.objects.filter(active=True)
    search = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    sort = request.GET.get("sort", "popular").strip()
    if search:
        products = products.filter(name__icontains=search)
    if category:
        products = products.filter(category=category)
    if sort == "points_low":
        products = products.order_by("points_required", "name")
    elif sort == "points_high":
        products = products.order_by("-points_required", "name")
    elif sort == "newest":
        products = products.order_by("-id")
    else:
        products = products.order_by("-id")
    return render(request, "user/rewards.html", {"rewards": products, "profile": request.user.profile, "categories": Reward.objects.filter(active=True).values_list("category", flat=True).distinct(), "search": search, "selected_category": category, "selected_sort": sort})


@login_required
def reward_purchase_history(request):
    purchases = Redemption.objects.filter(user=request.user).select_related("reward").order_by("-redeemed_at")
    return render(request, "user/reward_history.html", {"purchases": purchases, "profile": request.user.profile})


@login_required
def leaderboard(request):
    return render(request, "user/leaderboard.html", {"users": Profile.objects.select_related("user").order_by("-points")[:10]})


@login_required
def user_notifications_page(request):
    notification_queryset = Notification.objects.filter(user=request.user).order_by("-created_at")
    notifications = notification_queryset[:50]
    return render(request, "user/notifications.html", {"notifications": notifications, "unread_count": notification_queryset.filter(is_read=False).count()})


@login_required
def profile_page(request):
    profile, _ = Profile.objects.get_or_create(user=request.user)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "profile":
            name = request.POST.get("name", "").strip()
            email = request.POST.get("email", "").strip()
            if not name or not email:
                messages.error(request, "Name and email are required.")
            else:
                request.user.first_name = name
                request.user.email = email
                request.user.save(update_fields=["first_name", "email"])
                messages.success(request, "Profile updated successfully.")
                return redirect("profile")
        elif action == "password":
            password = request.POST.get("password", "")
            confirmation = request.POST.get("confirmation", "")
            if len(password) < 8 or password != confirmation:
                messages.error(request, "Passwords must match and contain at least 8 characters.")
            else:
                request.user.set_password(password)
                request.user.save(update_fields=["password"])
                login(request, request.user)
                messages.success(request, "Password changed successfully.")
                return redirect("profile")
        elif action == "lost_rfid":
            profile.rfid_status = "BLOCKED"
            profile.save(update_fields=["rfid_status"])
            messages.success(request, "Your RFID card was blocked. Ask an admin to activate it or register a new card.")
            return redirect("profile")
    sessions = RecyclingSession.objects.filter(user=request.user, status="COMPLETED")
    total_grams = sessions.aggregate(value=Sum("weight_grams"))["value"] or 0
    progress_kg = Decimal(total_grams) / 1000
    goal = profile.monthly_goal_kg or Decimal("5")
    return render(request, "user/profile.html", {
        "profile": profile,
        "rfid_card": RFIDCard.objects.filter(user=request.user, is_active=True).first(),
        "sessions_count": sessions.count(),
        "total_weight": progress_kg,
        "co2_saved": Decimal(total_grams) * Decimal("1.15") / 1000,
        "goal_progress": min(100, round(float(progress_kg / goal * 100), 1)),
        "goal_remaining": max(Decimal("0"), goal - progress_kg),
        "redemptions": Redemption.objects.filter(user=request.user).count(),
        "member_since": request.user.date_joined,
    })


def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    if request.method == "POST":
        identifier = request.POST.get("email", "").strip()
        account = User.objects.filter(email__iexact=identifier).first() or User.objects.filter(username=identifier).first()
        user = authenticate(request, username=account.username if account else identifier, password=request.POST.get("password"))
        if user and user.profile.status == "ACTIVE":
            login(request, user)
            return redirect("dashboard")
        messages.error(request, "Invalid credentials or blocked account.")
    return render(request, "auth/login.html")


def register_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        name = request.POST.get("name", "").strip()
        phone = normalize_phone(request.POST.get("phone"))
        password = request.POST.get("password", "")
        confirmation = request.POST.get("confirmation", "")
        if not email or not name or not phone or len(password) < 8:
            messages.error(request, "Enter your name, a valid 10-digit phone number, email, and a password of at least 8 characters.")
        elif password != confirmation:
            messages.error(request, "Passwords do not match.")
        elif User.objects.filter(username=email).exists():
            messages.error(request, "An account with this email already exists.")
        else:
            user = User.objects.create_user(username=email, email=email, password=password, first_name=name)
            Profile.objects.create(user=user, phone=phone)
            login(request, user)
            return redirect("dashboard")
    return render(request, "auth/register.html")


def logout_view(request):
    logout(request)
    return redirect("dashboard")


@login_required
def admin_dashboard(request):
    if not hasattr(request.user, "profile") or request.user.profile.role != "ADMIN":
        return redirect("dashboard")
    sessions = RecyclingSession.objects.select_related("user", "machine").order_by("-created_at")
    completed = sessions.filter(status="COMPLETED")
    today = timezone.localdate()
    week_start = today - timedelta(days=6)
    weekly_data = list(completed.filter(completed_at__date__gte=week_start, completed_at__date__lte=today).annotate(day=TruncDate("completed_at")).values("day").annotate(weight=Sum("weight_grams")).order_by("day"))
    weekly_by_day = {item["day"]: item["weight"] for item in weekly_data}
    weekly_weights = [weekly_by_day.get(week_start + timedelta(days=offset), 0) for offset in range(7)]
    weekly_max = max(weekly_weights, default=0)
    activity_points = " ".join(f"{offset * 116.67:.2f},{170 - (float(weight) / float(weekly_max) * 140 if weekly_max else 0):.2f}" for offset, weight in enumerate(weekly_weights))
    weekly_chart = [{"label": (week_start + timedelta(days=offset)).strftime("%a"), "kg": Decimal(weight) / 1000} for offset, weight in enumerate(weekly_weights)]
    return render(request, "admin/dashboard.html", {
        "users": User.objects.count(),
        "active_users": Profile.objects.filter(status="ACTIVE", role="USER").count(),
        "blocked_users": Profile.objects.filter(status="BLOCKED").count(),
        "paper": sessions.filter(status="COMPLETED").aggregate(value=Sum("weight_grams"))["value"] or 0,
        "paper_kg": Decimal(sessions.filter(status="COMPLETED").aggregate(value=Sum("weight_grams"))["value"] or 0) / 1000,
        "deposits": sessions.filter(status="COMPLETED").count(),
        "points": sessions.aggregate(value=Sum("points"))["value"] or 0,
        "machines": Machine.objects.all().order_by("code"),
        "online_machines": Machine.objects.filter(status="ONLINE").count(),
        "sessions": sessions[:8],
        "users_list": Profile.objects.select_related("user").order_by("-points")[:8],
        "rewards": Reward.objects.order_by("-active", "points_required")[:6],
        "reward_purchases": Redemption.objects.count(),
        "reward_points_redeemed": Redemption.objects.aggregate(value=Sum("points_used"))["value"] or 0,
        "recent_reward_purchases": Redemption.objects.select_related("user", "reward").order_by("-redeemed_at")[:5],
        "events": MachineEvent.objects.select_related("machine").order_by("-created_at")[:6],
        "weekly_chart": weekly_chart,
        "activity_points": activity_points,
        "admin_section": "dashboard",
        "page_title": "Dashboard",
    })


def admin_only(request):
    return request.user.is_authenticated and hasattr(request.user, "profile") and request.user.profile.role == "ADMIN"


@login_required
def admin_users_page(request):
    if not admin_only(request):
        return redirect("dashboard")
    search = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    users = Profile.objects.select_related("user")
    if search:
        users = users.filter(user__username__icontains=search) | Profile.objects.select_related("user").filter(user__email__icontains=search)
    if status in {"ACTIVE", "BLOCKED"}:
        users = users.filter(rfid_status=status)
    users = users.order_by("-points")
    user_ids = [profile.user_id for profile in users]
    deposit_counts = dict(RecyclingSession.objects.filter(user_id__in=user_ids).values("user_id").annotate(total=Count("id")).values_list("user_id", "total"))
    for profile in users:
        profile.deposit_count = deposit_counts.get(profile.user_id, 0)
    return render(request, "admin/users.html", {"users_list": users, "deposit_counts": deposit_counts, "search": search, "status": status, "total_users": Profile.objects.filter(role="USER").count(), "active_users": Profile.objects.filter(role="USER", rfid_status="ACTIVE").count(), "blocked_users": Profile.objects.filter(role="USER", rfid_status="BLOCKED").count(), "rfid_users": Profile.objects.filter(role="USER").exclude(rfid_uid__isnull=True).exclude(rfid_uid="").count(), "admin_section": "users", "page_title": "Users"})


@login_required
@require_http_methods(["POST"])
def admin_user_status_page(request, user_id):
    if not admin_only(request):
        return redirect("dashboard")
    status = request.POST.get("status", "").strip()
    if status not in {"ACTIVE", "BLOCKED"}:
        messages.error(request, "Choose either Active or Blocked for the user status.")
    else:
        profile = get_object_or_404(Profile, user_id=user_id)
        profile.status = status
        profile.save(update_fields=["status"])
        messages.success(request, f"{profile.user.get_full_name() or profile.user.username} is now {status.title()}.")
    return redirect(f"/admin-panel/users/?q={request.POST.get('q', '').strip()}&status={request.POST.get('filter_status', '').strip()}")


@login_required
@require_http_methods(["POST"])
def admin_user_rfid_status_page(request, user_id):
    if not admin_only(request):
        return redirect("dashboard")
    rfid_status = request.POST.get("rfid_status", "").strip()
    if rfid_status not in {"ACTIVE", "BLOCKED"}:
        messages.error(request, "Choose either Active or Blocked for the RFID status.")
    else:
        profile = get_object_or_404(Profile, user_id=user_id)
        profile.rfid_status = rfid_status
        profile.save(update_fields=["rfid_status"])
        messages.success(request, f"RFID status for {profile.user.get_full_name() or profile.user.username} is now {rfid_status.title()}.")
    return redirect(f"/admin-panel/users/?q={request.POST.get('q', '').strip()}&status={request.POST.get('filter_status', '').strip()}")


@login_required
def admin_user_detail_page(request, user_id):
    if not admin_only(request):
        return redirect("dashboard")
    profile = get_object_or_404(Profile.objects.select_related("user"), user_id=user_id)
    if request.method == "POST" and request.POST.get("action") == "manual_rfid":
        rfid_uid = request.POST.get("rfid_uid", "").strip() or None
        if rfid_uid and Profile.objects.filter(rfid_uid=rfid_uid).exclude(pk=profile.pk).exists():
            messages.error(request, "This RFID card is already linked to another user.")
        else:
            try:
                profile.rfid_uid = rfid_uid
                profile.rfid_status = "ACTIVE"
                profile.save(update_fields=["rfid_uid", "rfid_status"])
            except IntegrityError:
                messages.error(request, "This RFID card could not be saved because it is already linked.")
            else:
                messages.success(request, "RFID registration saved successfully.")
        return redirect("admin_user_detail_page", user_id=user_id)
    sessions = RecyclingSession.objects.filter(user=profile.user).select_related("machine").order_by("-created_at")
    total_weight = sessions.filter(status="COMPLETED").aggregate(value=Sum("weight_grams"))["value"] or 0
    registration = RFIDRegistration.objects.filter(profile=profile, status__in=["WAITING", "DETECTED"]).order_by("-created_at").first()
    return render(request, "admin/user_detail.html", {"profile": profile, "rfid_card": RFIDCard.objects.filter(user=profile.user, is_active=True).first(), "sessions": sessions, "total_weight": Decimal(total_weight) / 1000, "redemptions": Redemption.objects.filter(user=profile.user).count(), "registration": registration, "admin_section": "users", "page_title": "User details"})


@login_required
@require_http_methods(["POST"])
def start_rfid_registration(request, user_id):
    if not admin_only(request):
        return JsonResponse({"error": "Admin access required"}, status=403)
    profile = get_object_or_404(Profile, user_id=user_id)
    machine = Machine.objects.filter(status="ONLINE").order_by("code").first()
    if not machine:
        return JsonResponse({"success": False, "error": "No online machine is available"}, status=409)
    RFIDRegistration.objects.filter(profile=profile, status="WAITING").update(status="CANCELLED")
    registration = RFIDRegistration.objects.create(profile=profile, machine=machine)
    return JsonResponse({"success": True, "registration_id": registration.registration_id, "machine_code": machine.code, "status": registration.status})


@login_required
def rfid_registration_status(request, registration_id):
    if not admin_only(request):
        return JsonResponse({"error": "Admin access required"}, status=403)
    registration = get_object_or_404(RFIDRegistration, registration_id=registration_id)
    return JsonResponse({"registration_id": registration.registration_id, "status": registration.status, "rfid_uid": registration.rfid_uid, "user_id": registration.profile.user_id})


@login_required
def admin_machines_page(request):
    if not admin_only(request):
        return redirect("dashboard")
    machines = Machine.objects.all().order_by("code")
    return render(request, "admin/machines.html", {"machines": machines, "total": machines.count(), "online": machines.filter(status="ONLINE").count(), "offline": machines.filter(status="OFFLINE").count(), "maintenance": machines.filter(status="MAINTENANCE").count(), "admin_section": "machines", "page_title": "Machines"})


@login_required
def admin_machine_detail_page(request, machine_id):
    if not admin_only(request):
        return redirect("dashboard")
    machine = get_object_or_404(Machine, id=machine_id)
    sessions = RecyclingSession.objects.filter(machine=machine)
    return render(request, "admin/machine_detail.html", {"machine": machine, "sessions": sessions, "deposits": sessions.filter(status="COMPLETED").count(), "paper": sessions.filter(status="COMPLETED").aggregate(value=Sum("weight_grams"))["value"] or 0, "events": MachineEvent.objects.filter(machine=machine).order_by("-created_at")[:10], "admin_section": "machines", "page_title": "Machine details"})


@login_required
def admin_recycling_page(request):
    if not admin_only(request):
        return redirect("dashboard")
    status = request.GET.get("status", "").strip()
    sessions = RecyclingSession.objects.select_related("user", "machine").order_by("-created_at")
    if status:
        sessions = sessions.filter(status=status)
    completed = RecyclingSession.objects.filter(status="COMPLETED")
    paper = completed.aggregate(value=Sum("weight_grams"))["value"] or 0
    return render(request, "admin/recycling.html", {"sessions": sessions, "status": status, "total": RecyclingSession.objects.count(), "accepted": completed.count(), "rejected": RecyclingSession.objects.filter(status="REJECTED").count(), "paper": Decimal(paper) / 1000, "admin_section": "recycling", "page_title": "Recycling"})


@login_required
def admin_transaction_detail_page(request, session_id):
    if not admin_only(request):
        return redirect("dashboard")
    session = get_object_or_404(RecyclingSession.objects.select_related("user", "machine"), session_id=session_id)
    events = MachineEvent.objects.filter(machine=session.machine).order_by("created_at")[:12]
    return render(request, "admin/transaction_detail.html", {"session": session, "events": events, "admin_section": "recycling", "page_title": "Transaction details"})


@login_required
def admin_rewards_page(request):
    if not admin_only(request):
        return redirect("dashboard")
    return render(request, "admin/rewards.html", {"rewards": Reward.objects.order_by("-active", "points_required"), "redemptions": Redemption.objects.select_related("user", "reward").order_by("-redeemed_at")[:12], "most_redeemed": Reward.objects.annotate(redemption_count=Count("redemption")).filter(redemption_count__gt=0).order_by("-redemption_count")[:5], "purchase_count": Redemption.objects.count(), "points_redeemed": Redemption.objects.aggregate(value=Sum("points_used"))["value"] or 0, "admin_section": "rewards", "page_title": "Rewards"})


@login_required
def admin_reward_edit_page(request, reward_id):
    if not admin_only(request):
        return redirect("dashboard")
    reward = get_object_or_404(Reward, id=reward_id)
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        try:
            points_required = int(request.POST.get("points_required", "0"))
            stock = int(request.POST.get("stock", "0"))
        except (TypeError, ValueError):
            points_required = 0
            stock = -1
        if not name or points_required < 1 or stock < 0:
            messages.error(request, "Enter a reward name, valid points, and quantity.")
        else:
            reward.name = name
            reward.description = request.POST.get("description", "").strip()
            reward.category = request.POST.get("category", "Eco Products").strip() or "Eco Products"
            reward.image_url = request.POST.get("image_url", "").strip() or reward.image_url
            if request.FILES.get("image"):
                reward.image = request.FILES["image"]
            reward.points_required = points_required
            reward.stock = stock
            reward.active = request.POST.get("active") == "on"
            reward.save()
            if reward.image:
                reward.image_url = reward.image.url
                reward.save(update_fields=["image_url"])
            messages.success(request, f"{reward.name} was updated successfully.")
            return redirect("admin_rewards_page")
    return render(request, "admin/reward_form.html", {"reward": reward, "image_choices": REWARD_IMAGE_CHOICES, "admin_section": "rewards", "page_title": "Edit reward"})


@login_required
@require_http_methods(["POST"])
def admin_reward_toggle_page(request, reward_id):
    if not admin_only(request):
        return redirect("dashboard")
    reward = get_object_or_404(Reward, id=reward_id)
    reward.active = not reward.active
    reward.save(update_fields=["active"])
    messages.success(request, f"{reward.name} is now {'enabled' if reward.active else 'disabled'}.")
    return redirect("admin_rewards_page")


@login_required
def admin_reward_form_page(request):
    if not admin_only(request):
        return redirect("dashboard")
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        description = request.POST.get("description", "").strip()
        category = request.POST.get("category", "Eco Products").strip() or "Eco Products"
        image_url = request.POST.get("image_url", "").strip()
        try:
            points_required = int(request.POST.get("points_required", "0"))
            stock = int(request.POST.get("stock", "0"))
        except (TypeError, ValueError):
            points_required = 0
            stock = 0
        if not name or points_required < 1 or stock < 0:
            messages.error(request, "Enter a reward name, valid points, and quantity.")
        else:
            reward = Reward.objects.create(
                name=name,
                description=description,
                category=category,
                image_url=image_url,
                image=request.FILES.get("image"),
                points_required=points_required,
                stock=stock,
                active=request.POST.get("active") == "on",
            )
            if reward.image:
                reward.image_url = reward.image.url
                reward.save(update_fields=["image_url"])
            messages.success(request, f"{name} was created successfully.")
            return redirect("admin_rewards_page")
    return render(request, "admin/reward_form.html", {"image_choices": REWARD_IMAGE_CHOICES, "admin_section": "rewards", "page_title": "Create reward"})


@login_required
def admin_reports_page(request):
    if not admin_only(request):
        return redirect("dashboard")
    completed = RecyclingSession.objects.filter(status="COMPLETED")
    paper = completed.aggregate(value=Sum("weight_grams"))["value"] or 0
    average_weight = completed.aggregate(value=Avg("weight_grams"))["value"] or 0
    today = timezone.localdate()
    first_completed = completed.filter(completed_at__isnull=False).order_by("completed_at").values_list("completed_at", flat=True).first()
    first_month = timezone.localtime(first_completed).date().replace(day=1) if first_completed else today.replace(day=1)
    requested_month = request.GET.get("month", "")
    try:
        selected_month = datetime.strptime(requested_month, "%Y-%m").date().replace(day=1) if requested_month else today.replace(day=1)
    except ValueError:
        selected_month = today.replace(day=1)
    if selected_month < first_month or selected_month > today.replace(day=1):
        selected_month = today.replace(day=1)
    next_month = (selected_month.replace(day=28) + timedelta(days=4)).replace(day=1)
    last_day = next_month - timedelta(days=1)
    daily_data = list(completed.filter(completed_at__date__gte=selected_month, completed_at__date__lte=last_day).annotate(day=TruncDate("completed_at")).values("day").annotate(weight=Sum("weight_grams")).order_by("day"))
    daily_by_day = {item["day"]: item["weight"] for item in daily_data}
    chart_data = [{"date": selected_month.replace(day=day).isoformat(), "label": str(day), "grams": float(daily_by_day.get(selected_month.replace(day=day), 0) or 0)} for day in range(1, last_day.day + 1)]
    available_months = []
    cursor = today.replace(day=1)
    while cursor >= first_month:
        available_months.append({"value": cursor.strftime("%Y-%m"), "label": cursor.strftime("%B %Y")})
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    logger.info("Admin report monthly paper dataset month=%s points=%s", selected_month.strftime("%Y-%m"), chart_data)
    context = {"new_users": User.objects.filter(date_joined__date=timezone.localdate()).count(), "active_users": Profile.objects.filter(status="ACTIVE", role="USER").count(), "rfid_users": Profile.objects.exclude(rfid_uid__isnull=True).exclude(rfid_uid="").count(), "paper": Decimal(paper) / 1000, "deposits": completed.count(), "rejected": RecyclingSession.objects.filter(status="REJECTED").count(), "redemptions": Redemption.objects.count(), "points": completed.aggregate(value=Sum("points"))["value"] or 0, "average_weight": average_weight, "chart_data": chart_data, "selected_month": selected_month.strftime("%Y-%m"), "selected_month_label": selected_month.strftime("%B %Y"), "available_months": available_months, "admin_section": "reports", "page_title": "Reports & analytics"}
    if request.headers.get("X-Chart-Request") == "true":
        return JsonResponse({"month": context["selected_month_label"], "chart_data": chart_data})
    return render(request, "admin/reports.html", context)


@login_required
def admin_settings_page(request):
    if not admin_only(request):
        return redirect("dashboard")
    if request.method == "POST":
        request.user.first_name = request.POST.get("admin_name", "").strip()
        request.user.email = request.POST.get("admin_email", "").strip()
        request.user.save(update_fields=["first_name", "email"])
        try:
            grams_per_point = Decimal(request.POST.get("grams_per_point", "1"))
        except (InvalidOperation, TypeError):
            grams_per_point = Decimal("0")
        if grams_per_point <= 0:
            messages.error(request, "Weight required must be greater than 0 grams.")
            return redirect("admin_settings_page")
        RecyclingPointConfig.objects.update(active=False)
        RecyclingPointConfig.objects.create(grams_per_point=grams_per_point, active=True)
        new_password = request.POST.get("new_password", "")
        confirmation = request.POST.get("password_confirmation", "")
        if new_password:
            if len(new_password) < 8 or new_password != confirmation:
                messages.error(request, "Password must be at least 8 characters and match confirmation.")
                return redirect("admin_settings_page")
            request.user.set_password(new_password)
            request.user.save(update_fields=["password"])
            login(request, request.user)
        messages.success(request, "Settings saved successfully.")
        return redirect("admin_settings_page")
    point_config = RecyclingPointConfig.objects.filter(active=True).order_by("-updated_at", "-id").first()
    return render(request, "admin/settings.html", {"point_config": point_config, "admin_section": "settings", "page_title": "Settings"})


@login_required
def admin_notifications_page(request):
    if not admin_only(request):
        return redirect("dashboard")
    events = MachineEvent.objects.select_related("machine").order_by("-created_at")[:30]
    return render(request, "admin/notifications.html", {"events": events, "unread": events.count(), "admin_section": "notifications", "page_title": "Notifications"})


def machine_request(request):
    data = json_body(request)
    if data is None:
        return None, JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)
    machine_code = data.get("machine_id") or data.get("device_id")
    api_key = request.headers.get("X-Machine-API-Key") or data.get("api_key")
    machine = Machine.objects.filter(code=machine_code, api_key=api_key).first()
    if not machine:
        return None, JsonResponse({"success": False, "error": "Machine authentication failed"}, status=401)
    if machine.status != "ONLINE":
        return None, JsonResponse({"success": False, "error": "Machine is offline"}, status=409)
    machine.last_seen = timezone.now()
    machine.save(update_fields=["last_seen"])
    MachineEvent.objects.create(machine=machine, event_type="REQUEST", data={"path": request.path})
    return (machine, data), None


@csrf_exempt
@require_http_methods(["POST"])
def rfid_registration_scan(request):
    result, error = machine_request(request)
    if error:
        return error
    machine, data = result
    registration = RFIDRegistration.objects.filter(registration_id=data.get("registration_id"), machine=machine, status="WAITING").select_related("profile").first()
    if not registration:
        return JsonResponse({"success": False, "error": "RFID registration is not waiting on this machine"}, status=404)
    uid = str(data.get("rfid_uid", "")).strip()
    if not uid:
        return JsonResponse({"success": False, "error": "RFID UID is required"}, status=400)
    existing = Profile.objects.filter(rfid_uid=uid).exclude(pk=registration.profile_id).first()
    if existing:
        registration.status = "CANCELLED"
        registration.save(update_fields=["status"])
        return JsonResponse({"success": False, "error": "This RFID card is already linked to another user"}, status=409)
    registration.rfid_uid = uid
    registration.status = "DETECTED"
    registration.save(update_fields=["rfid_uid", "status"])
    registration.profile.rfid_uid = uid
    registration.profile.rfid_status = "ACTIVE"
    registration.profile.save(update_fields=["rfid_uid", "rfid_status"])
    RFIDCard.objects.update_or_create(uid=uid, defaults={"user_id": registration.profile.user_id, "is_active": True})
    registration.status = "LINKED"
    registration.linked_at = timezone.now()
    registration.save(update_fields=["status", "linked_at"])
    return JsonResponse({"success": True, "event": "RFID_LINKED", "registration_id": registration.registration_id, "rfid_uid": uid, "status": registration.status, "user_id": registration.profile.user_id})


@csrf_exempt
@require_http_methods(["POST"])
def rfid_scan(request):
    result, error = machine_request(request)
    if error:
        return error
    machine, data = result
    card_data = data.get("data") if isinstance(data.get("data"), dict) else data
    uid = str(card_data.get("rfid_uid", "")).strip().upper()
    card_id = str(card_data.get("card_id", "")).strip() or None
    name = str(card_data.get("name", "")).strip()
    phone = normalize_phone(card_data.get("phone")) if card_data.get("phone") else None
    if not uid:
        return JsonResponse({"success": False, "status": "RFID_REJECTED", "error": "RFID UID is required"}, status=400)
    card = RFIDCard.objects.select_related("user").filter(uid=uid).first()
    if card_id and RFIDCard.objects.filter(card_id=card_id).exclude(uid=uid).exists():
        return JsonResponse({"success": False, "status": "RFID_CONFLICT", "error": "Card ID is already linked to another RFID card"}, status=409)
    if card:
        profile = Profile.objects.select_related("user").get(user=card.user)
        if card_id and card.card_id and card.card_id != card_id:
            return JsonResponse({"success": False, "status": "RFID_CONFLICT", "error": "RFID UID is linked to a different card ID"}, status=409)
        is_new_user = False
    else:
        if not phone:
            return JsonResponse({"success": False, "status": "RFID_REJECTED", "error": "A valid phone number is required to identify this card owner"}, status=400)
        profile = Profile.objects.select_related("user").filter(phone=phone).first()
        if not profile:
            return JsonResponse({"success": False, "status": "RFID_REJECTED", "error": "Phone number does not belong to a registered user"}, status=403)
        if profile.rfid_uid and profile.rfid_uid != uid:
            return JsonResponse({"success": False, "status": "RFID_CONFLICT", "error": "This user already has a different RFID card linked"}, status=409)
        card = RFIDCard.objects.create(uid=uid, card_id=card_id, user=profile.user)
        profile.rfid_uid = uid
        profile.rfid_status = "ACTIVE"
        profile.save(update_fields=["rfid_uid", "rfid_status"])
        is_new_user = False
    if not card.is_active or profile.rfid_status == "BLOCKED" or profile.status == "BLOCKED" or not card.user.is_active:
        return JsonResponse({"success": False, "status": "RFID_REJECTED", "reason": "RFID_BLOCKED", "error": "RFID card or account is blocked"}, status=403)
    active_statuses = ["READY_FOR_DEPOSIT", "MEASURING", "PROCESSING"]
    session = RecyclingSession.objects.filter(user=card.user, machine=machine, rfid_uid=uid, status__in=active_statuses).order_by("-created_at").first()
    if not session:
        session = RecyclingSession.objects.create(user=card.user, machine=machine, rfid_card=card, rfid_uid=uid, status="READY_FOR_DEPOSIT")
        notify(profile.user, "Card verified", f"Machine {machine.code} is ready for your paper.")
    payload = session_payload(session)
    return JsonResponse({"success": True, "event": "RFID_VERIFIED", "session_id": payload["session_id"], "user_id": payload["user_id"], "machine_id": payload["machine_id"], "machine_code": payload["machine_code"], "rfid_uid": payload["rfid_uid"], "status": payload["status"], "weight": payload["weight"], "points": payload["points"], "is_new_user": is_new_user, "user": {"id": profile.user_id, "name": profile.user.get_full_name() or profile.user.username, "phone": profile.phone}, "message": "RFID verified. Recycling session ready."})


@csrf_exempt
@require_http_methods(["POST"])
def weight_received(request, machine_id=None):
    result, error = machine_request(request)
    if error:
        return error
    machine, data = result
    event_data = data.get("data") if isinstance(data.get("data"), dict) else data
    session = RecyclingSession.objects.select_related("rfid_card").filter(session_id=data.get("session_id"), machine=machine).first()
    if not session:
        return JsonResponse({"success": False, "error": "Session not found"}, status=404)
    if session.status != "READY_FOR_DEPOSIT":
        return JsonResponse({"success": False, "error": "Session is not ready for a weight deposit"}, status=409)
    try:
        weight = Decimal(str(event_data.get("weight", event_data.get("weight_g", 0))))
    except (InvalidOperation, TypeError):
        weight = Decimal("0")
    if weight <= 0 or weight > 500:
        session.status = "REJECTED"
        session.save(update_fields=["status"])
        return JsonResponse({"success": False, "event": "REJECTED", "error": "Weight must be between 0 and 500 grams"}, status=422)
    session.weight_grams = weight
    session.points = points_for_weight(weight)
    session.status = "MEASURING"
    session.save(update_fields=["weight_grams", "points", "status"])
    return JsonResponse({"success": True, "event": "WEIGHT_MEASURED", **session_payload(session)})


@csrf_exempt
@require_http_methods(["POST"])
def processing(request, machine_id=None):
    result, error = machine_request(request)
    if error:
        return error
    machine, data = result
    session = get_object_or_404(RecyclingSession, session_id=data.get("session_id"), machine=machine)
    if session.status != "MEASURING":
        return JsonResponse({"success": False, "error": "Weight must be accepted before processing"}, status=409)
    session.status = "PROCESSING"
    session.save(update_fields=["status"])
    return JsonResponse({"success": True, "event": "PROCESSING", **session_payload(session)})


@csrf_exempt
@require_http_methods(["POST"])
def session_complete(request, machine_id=None):
    result, error = machine_request(request)
    if error:
        return error
    machine, data = result
    session = get_object_or_404(RecyclingSession, session_id=data.get("session_id"), machine=machine)
    if session.status == "COMPLETED":
        profile = session.user.profile
        return JsonResponse({"success": True, "event": "COMPLETED", **session_payload(session), "total_points": profile.points, "idempotent": True})
    if session.status not in {"MEASURING", "PROCESSING"}:
        return JsonResponse({"success": False, "error": "Session is not ready to complete"}, status=409)
    session.status = "COMPLETED"
    session.completed_at = timezone.now()
    session.save(update_fields=["status", "completed_at"])
    profile = session.user.profile
    profile.points += session.points
    profile.save(update_fields=["points"])
    notify(session.user, "Recycling successful", f"{session.weight_grams} g recycled and {session.points} points added.")
    return JsonResponse({"success": True, "event": "COMPLETED", **session_payload(session), "total_points": profile.points})


@csrf_exempt
@require_http_methods(["POST"])
def heartbeat(request, machine_id=None):
    result, error = machine_request(request)
    if error:
        return error
    machine, data = result
    try:
        bin_level = int(data.get("bin_level", machine.bin_level))
    except (TypeError, ValueError):
        return JsonResponse({"success": False, "error": "bin_level must be an integer"}, status=400)
    if not 0 <= bin_level <= 100:
        return JsonResponse({"success": False, "error": "bin_level must be between 0 and 100"}, status=422)
    machine.bin_level = bin_level
    machine.save(update_fields=["bin_level"])
    if machine.bin_level >= 90:
        MachineEvent.objects.create(machine=machine, event_type="BIN_ALMOST_FULL", data={"bin_level": machine.bin_level})
    return JsonResponse({"success": True, "event": "HEARTBEAT", "status": machine.status, "bin_level": machine.bin_level})


@csrf_exempt
@require_http_methods(["POST"])
def machine_event(request, machine_id=None):
    result, error = machine_request(request)
    if error:
        return error
    machine, data = result
    event = MachineEvent.objects.create(machine=machine, event_type=data.get("event") or data.get("event_type", "ERROR"), data=data.get("data", {}))
    return JsonResponse({"success": True, "event_id": event.id})


@csrf_exempt
@require_http_methods(["POST"])
def api_register(request):
    data = json_body(request) or {}
    phone = normalize_phone(data.get("phone"))
    if not data.get("email") or not data.get("password") or not phone:
        return JsonResponse({"success": False, "error": "Email, password, and a valid 10-digit phone number are required"}, status=400)
    if User.objects.filter(username=data["email"]).exists():
        return JsonResponse({"success": False, "error": "Email already registered"}, status=409)
    user = User.objects.create_user(username=data["email"], email=data["email"], password=data["password"], first_name=data.get("name", ""))
    Profile.objects.create(user=user, phone=phone, rfid_uid=data.get("rfid_uid"), rfid_status="ACTIVE")
    login(request, user)
    return JsonResponse({"success": True, "user": user_payload(user)}, status=201)


@csrf_exempt
@require_http_methods(["POST"])
def api_login(request):
    data = json_body(request) or {}
    user = authenticate(request, username=data.get("email"), password=data.get("password"))
    if not user or user.profile.status == "BLOCKED":
        return JsonResponse({"success": False, "error": "Invalid credentials"}, status=401)
    login(request, user)
    return JsonResponse({"success": True, "user": user_payload(user)})


@login_required
def api_me(request):
    return JsonResponse({"user": user_payload(request.user)})


@login_required
def api_user_dashboard(request):
    sessions = RecyclingSession.objects.filter(user=request.user, status="COMPLETED")
    total = sessions.aggregate(value=Sum("weight_grams"))["value"] or 0
    return JsonResponse({"points": request.user.profile.points, "recycled_kg": float(Decimal(total) / 1000), "deposits": sessions.count(), "co2_saved_kg": float(Decimal(total) * Decimal("1.15") / 1000)})


@login_required
def api_history(request, transaction_id=None):
    sessions = RecyclingSession.objects.filter(user=request.user).select_related("machine").order_by("-created_at")
    if transaction_id:
        sessions = sessions.filter(id=transaction_id)
    return JsonResponse({"sessions": [session_payload(session) for session in sessions]})


@login_required
def api_session(request, session_id):
    session = RecyclingSession.objects.filter(session_id=session_id, user=request.user).first()
    if not session:
        exists = RecyclingSession.objects.filter(session_id=session_id).exists()
        logger.warning("Session lookup failed: user_id=%s session_exists=%s", request.user.id, exists)
        return JsonResponse({"error": "Session not found"}, status=404)
    return JsonResponse({"event": session.status, "session": session_payload(session), "total_points": request.user.profile.points})


@login_required
@require_http_methods(["POST"])
def redeem_reward(request, reward_id):
    with transaction.atomic():
        reward = Reward.objects.select_for_update().filter(id=reward_id, active=True).first()
        profile = Profile.objects.select_for_update().get(user=request.user)
        if not reward:
            return JsonResponse({"success": False, "error": "Reward is no longer available"}, status=404)
        if reward.stock < 1:
            return JsonResponse({"success": False, "error": "Reward is out of stock"}, status=422)
        if profile.points < reward.points_required:
            return JsonResponse({"success": False, "error": f"Need {reward.points_required - profile.points} more points"}, status=422)
        profile.points -= reward.points_required
        profile.save(update_fields=["points"])
        reward.stock -= 1
        reward.save(update_fields=["stock"])
        redemption = Redemption.objects.create(user=request.user, reward=reward, points_used=reward.points_required, coupon_code=f"ECO-{secrets.token_hex(4).upper()}")
    notify(request.user, "Reward redeemed", f"Your {reward.name} has been added to your rewards.", "REDEMPTION")
    return JsonResponse({"success": True, "transaction_id": redemption.transaction_id, "product": reward.name, "points_spent": redemption.points_used, "points": profile.points, "remaining_points": profile.points, "remaining_stock": reward.stock, "status": redemption.status})


@login_required
def api_rewards(request):
    return JsonResponse({"rewards": list(Reward.objects.filter(active=True).values("id", "name", "description", "category", "image_url", "points_required", "stock"))})


@login_required
def api_redemptions(request):
    return JsonResponse({"redemptions": list(Redemption.objects.filter(user=request.user).values("id", "transaction_id", "reward__name", "points_used", "status", "coupon_code", "redeemed_at"))})


@login_required
def api_notifications(request):
    notifications = Notification.objects.filter(user=request.user).order_by("-created_at")[:50]
    return JsonResponse({"notifications": list(notifications.values("id", "type", "title", "message", "is_read", "created_at"))})


@login_required
@require_http_methods(["POST"])
def read_notification(request, notification_id):
    notification = get_object_or_404(Notification, id=notification_id, user=request.user)
    notification.is_read = True
    notification.save(update_fields=["is_read"])
    return JsonResponse({"success": True})


@login_required
def api_leaderboard(request):
    users = Profile.objects.filter(status="ACTIVE").select_related("user").order_by("-points")[:20]
    return JsonResponse({"leaderboard": [{"rank": index, "name": profile.user.get_full_name() or profile.user.username, "points": profile.points} for index, profile in enumerate(users, 1)]})


@login_required
def api_admin_dashboard(request):
    if request.user.profile.role != "ADMIN":
        return JsonResponse({"error": "Admin access required"}, status=403)
    return JsonResponse({"users": User.objects.count(), "machines": Machine.objects.count(), "sessions": RecyclingSession.objects.count(), "points": RecyclingSession.objects.aggregate(value=Sum("points"))["value"] or 0, "reward_purchases": Redemption.objects.count(), "reward_points_redeemed": Redemption.objects.aggregate(value=Sum("points_used"))["value"] or 0})


@login_required
def api_admin_users(request, user_id=None):
    if request.user.profile.role != "ADMIN":
        return JsonResponse({"error": "Admin access required"}, status=403)
    users = User.objects.select_related("profile")
    if user_id:
        return JsonResponse({"user": user_payload(get_object_or_404(users, id=user_id))})
    return JsonResponse({"users": [user_payload(user) for user in users]})


@login_required
@require_http_methods(["POST"])
def set_user_status(request, user_id, status):
    if request.user.profile.role != "ADMIN":
        return JsonResponse({"error": "Admin access required"}, status=403)
    profile = get_object_or_404(Profile, user_id=user_id)
    profile.status = status
    profile.save(update_fields=["status"])
    return JsonResponse({"success": True, "status": profile.status})


@login_required
def api_admin_machines(request, machine_id=None):
    if request.user.profile.role != "ADMIN":
        return JsonResponse({"error": "Admin access required"}, status=403)
    machines = Machine.objects.all()
    if machine_id:
        machines = machines.filter(id=machine_id)
    return JsonResponse({"machines": list(machines.values("id", "code", "location", "status", "bin_level", "last_seen"))})


@login_required
def api_admin_recycling(request, suspicious=False):
    if request.user.profile.role != "ADMIN":
        return JsonResponse({"error": "Admin access required"}, status=403)
    sessions = RecyclingSession.objects.select_related("user", "machine").order_by("-created_at")
    if suspicious:
        sessions = sessions.filter(weight_grams__gt=500)
    return JsonResponse({"sessions": [session_payload(session) for session in sessions]})


@login_required
def api_admin_analytics(request):
    if request.user.profile.role != "ADMIN":
        return JsonResponse({"error": "Admin access required"}, status=403)
    return JsonResponse({"daily_sessions": list(RecyclingSession.objects.values("created_at__date").annotate(total=Count("id")).order_by("created_at__date")[:30])})


@login_required
@require_http_methods(["GET", "PUT"])
def api_profile(request):
    profile, _ = Profile.objects.get_or_create(user=request.user)
    if request.method == "PUT":
        data = json_body(request) or {}
        request.user.first_name = data.get("name", request.user.first_name)
        request.user.email = data.get("email", request.user.email)
        request.user.save(update_fields=["first_name", "email"])
        profile.monthly_goal_kg = data.get("monthly_goal_kg", profile.monthly_goal_kg)
        profile.save(update_fields=["monthly_goal_kg"])
    return JsonResponse({"user": user_payload(request.user), "monthly_goal_kg": float(profile.monthly_goal_kg)})


@login_required
def api_analytics(request):
    sessions = RecyclingSession.objects.filter(user=request.user, status="COMPLETED")
    return JsonResponse({"daily": list(sessions.values("created_at__date").annotate(weight=Sum("weight_grams")).order_by("created_at__date"))})


@login_required
def api_goals(request):
    profile = request.user.profile
    total = RecyclingSession.objects.filter(user=request.user, status="COMPLETED").aggregate(value=Sum("weight_grams"))["value"] or 0
    return JsonResponse({"goal_kg": float(profile.monthly_goal_kg), "progress_kg": float(Decimal(total) / 1000), "percentage": min(100, round(float(Decimal(total) / 10 / profile.monthly_goal_kg), 1))})


@login_required
def api_achievements(request):
    total = RecyclingSession.objects.filter(user=request.user, status="COMPLETED").aggregate(value=Sum("weight_grams"))["value"] or 0
    return JsonResponse({"achievements": [{"name": "First deposit", "unlocked": RecyclingSession.objects.filter(user=request.user, status="COMPLETED").exists()}, {"name": "First kilogram", "unlocked": total >= 1000}]})


@csrf_exempt
@require_http_methods(["POST"])
def api_forgot_password(request):
    data = json_body(request) or {}
    exists = User.objects.filter(username=data.get("email")).exists()
    return JsonResponse({"success": True, "message": "If the account exists, an OTP has been sent.", "account_found": exists})


@csrf_exempt
@require_http_methods(["POST"])
def api_verify_otp(request):
    return JsonResponse({"success": True, "verified": True})


@csrf_exempt
@require_http_methods(["POST"])
def api_reset_password(request):
    data = json_body(request) or {}
    user = User.objects.filter(username=data.get("email")).first()
    if not user or not data.get("password"):
        return JsonResponse({"success": False, "error": "Invalid reset request"}, status=400)
    user.set_password(data["password"])
    user.save(update_fields=["password"])
    return JsonResponse({"success": True})


@login_required
@require_http_methods(["POST"])
def api_start_recycling(request):
    data = json_body(request) or {}
    machine = get_object_or_404(Machine, id=data.get("machine_id"), status="ONLINE")
    profile = request.user.profile
    if profile.status == "BLOCKED" or profile.rfid_status == "BLOCKED" or not profile.rfid_uid:
        return JsonResponse({"success": False, "error": "Register an active RFID card first"}, status=403)
    card = RFIDCard.objects.filter(user=request.user, uid=profile.rfid_uid, is_active=True).first()
    if not card:
        return JsonResponse({"success": False, "error": "Register an active RFID card first"}, status=403)
    request.session["recycling_machine_id"] = machine.id
    request.session.modified = True
    return JsonResponse({"success": True, "event": "WAITING_FOR_RFID", "machine_id": machine.id, "machine_code": machine.code, "message": "Please tap your RFID card on the machine."})


@login_required
def api_active_session(request):
    session_filter = {"user": request.user, "status__in": ["READY_FOR_DEPOSIT", "MEASURING", "PROCESSING"]}
    machine_id = request.session.get("recycling_machine_id")
    if machine_id:
        session_filter["machine_id"] = machine_id
    session = RecyclingSession.objects.filter(**session_filter).select_related("machine", "rfid_card").order_by("-created_at").first()
    if session:
        request.session.pop("recycling_machine_id", None)
        request.session.modified = True
    return JsonResponse({"success": True, "session": session_payload(session) if session else None})


@login_required
def api_admin_rewards(request, reward_id=None):
    if request.user.profile.role != "ADMIN":
        return JsonResponse({"error": "Admin access required"}, status=403)
    rewards = Reward.objects.all()
    if reward_id:
        rewards = rewards.filter(id=reward_id)
    return JsonResponse({"rewards": list(rewards.values("id", "name", "description", "points_required", "stock", "active"))})


@login_required
@require_http_methods(["POST", "PUT"])
def save_admin_reward(request, reward_id=None):
    if request.user.profile.role != "ADMIN":
        return JsonResponse({"error": "Admin access required"}, status=403)
    data = json_body(request) or {}
    reward = get_object_or_404(Reward, id=reward_id) if reward_id else Reward()
    for field in ["name", "description", "category", "image_url", "points_required", "stock", "active"]:
        if field in data:
            setattr(reward, field, data[field])
    reward.save()
    return JsonResponse({"success": True, "reward_id": reward.id})


@login_required
@require_http_methods(["POST"])
def toggle_admin_reward(request, reward_id, active):
    if request.user.profile.role != "ADMIN":
        return JsonResponse({"error": "Admin access required"}, status=403)
    reward = get_object_or_404(Reward, id=reward_id)
    reward.active = active
    reward.save(update_fields=["active"])
    return JsonResponse({"success": True, "active": reward.active})


@login_required
@require_http_methods(["POST"])
def create_admin_machine(request):
    if request.user.profile.role != "ADMIN":
        return JsonResponse({"error": "Admin access required"}, status=403)
    data = json_body(request) or {}
    machine = Machine.objects.create(code=data["code"], location=data.get("location", "Main Campus"), api_key=data.get("api_key", secrets.token_urlsafe(24)))
    return JsonResponse({"success": True, "machine_id": machine.id}, status=201)


@login_required
def api_admin_reports(request):
    if request.user.profile.role != "ADMIN":
        return JsonResponse({"error": "Admin access required"}, status=403)
    completed = RecyclingSession.objects.filter(status="COMPLETED")
    return JsonResponse({"completed_sessions": completed.count(), "total_weight_grams": completed.aggregate(value=Sum("weight_grams"))["value"] or 0, "total_points": completed.aggregate(value=Sum("points"))["value"] or 0, "reward_purchases": Redemption.objects.count(), "reward_points_redeemed": Redemption.objects.aggregate(value=Sum("points_used"))["value"] or 0})


@login_required
@require_http_methods(["POST", "DELETE"])
def admin_user_rfid(request, user_id):
    if request.user.profile.role != "ADMIN":
        return JsonResponse({"error": "Admin access required"}, status=403)
    profile = get_object_or_404(Profile, user_id=user_id)
    if request.method == "DELETE":
        profile.rfid_uid = None
        profile.rfid_status = "ACTIVE"
    else:
        data = json_body(request) or {}
        profile.rfid_uid = str(data.get("rfid_uid", "")).strip().upper() or None
        profile.rfid_status = "ACTIVE"
    profile.save(update_fields=["rfid_uid", "rfid_status"])
    if profile.rfid_uid:
        RFIDCard.objects.update_or_create(uid=profile.rfid_uid, defaults={"user_id": profile.user_id, "is_active": True})
    else:
        RFIDCard.objects.filter(user_id=profile.user_id).update(is_active=False)
    return JsonResponse({"success": True, "rfid_uid": profile.rfid_uid, "rfid_status": profile.rfid_status})
