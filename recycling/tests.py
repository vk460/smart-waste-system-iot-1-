import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from .models import Machine, Notification, Profile, RFIDCard, RecyclingPointConfig, RecyclingSession, RewardRule


class RFIDMachineFlowTests(TestCase):
    def setUp(self):
        self.machine = Machine.objects.create(code="MACHINE_TEST", api_key="test-key", status="ONLINE")
        RecyclingPointConfig.objects.filter(active=True).update(active=False)
        RecyclingPointConfig.objects.create(grams_per_point=1, active=True)
        self.client.defaults["CONTENT_TYPE"] = "application/json"

    def post_json(self, path, payload):
        return self.client.post(path, data=json.dumps(payload), content_type="application/json")

    def machine_payload(self, event, data=None, **extra):
        payload = {"device_id": self.machine.code, "api_key": self.machine.api_key, "event": event}
        if data is not None:
            payload["data"] = data
        payload.update(extra)
        return payload

    def test_new_card_creates_user_card_and_session(self):
        user = User.objects.create_user(username="kachan@example.com", first_name="Kachan More")
        Profile.objects.create(user=user, phone="9876543210", points=0)
        response = self.post_json("/api/machines/rfid-scan/", self.machine_payload("rfid_detected", {"rfid_uid": "A3F82C19", "card_id": "RF001", "name": "Kachan More", "phone": "9876543210"}))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["is_new_user"])
        self.assertEqual(body["event"], "RFID_VERIFIED")
        self.assertEqual(body["status"], "READY_FOR_DEPOSIT")
        self.assertEqual(body["user"]["id"], user.id)
        self.assertEqual(RFIDCard.objects.count(), 1)
        self.assertEqual(RecyclingSession.objects.count(), 1)
        self.assertEqual(Profile.objects.get(user=user).points, 0)

    def test_existing_card_creates_session_without_duplicate_user(self):
        user = User.objects.create_user(username="maya@example.com", first_name="Maya Singh")
        Profile.objects.create(user=user, phone="9876543211")
        first = self.post_json("/api/machines/rfid-scan/", self.machine_payload("rfid_detected", {"rfid_uid": "A3F82C20", "card_id": "RF002", "name": "Maya Singh", "phone": "9876543211"}))
        second = self.post_json("/api/machines/rfid-scan/", self.machine_payload("rfid_detected", {"rfid_uid": "A3F82C20", "card_id": "RF002", "name": "Maya Singh", "phone": "9876543211"}))
        self.assertFalse(first.json()["is_new_user"])
        self.assertFalse(second.json()["is_new_user"])
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(first.json()["session_id"], second.json()["session_id"])
        self.assertEqual(RecyclingSession.objects.count(), 1)

    def test_weight_belongs_to_card_session_and_completion_is_idempotent(self):
        user = User.objects.create_user(username="asha@example.com", first_name="Asha Rao")
        Profile.objects.create(user=user, phone="9876543212")
        scan = self.post_json("/api/machines/rfid-scan/", self.machine_payload("rfid_detected", {"rfid_uid": "A3F82C21", "card_id": "RF003", "name": "Asha Rao", "phone": "9876543212"}))
        session_id = scan.json()["session_id"]
        weight = self.post_json("/api/machines/weight/", self.machine_payload("weight_stable", {"weight_g": 50}, session_id=session_id))
        self.assertEqual(weight.json()["user_id"], scan.json()["user_id"])
        self.assertEqual(weight.json()["points"], 50)
        complete = self.post_json("/api/machines/session-complete/", self.machine_payload("deposit_completed", session_id=session_id))
        retry = self.post_json("/api/machines/session-complete/", self.machine_payload("deposit_completed", session_id=session_id))
        self.assertEqual(complete.status_code, 200)
        self.assertTrue(retry.json()["idempotent"])
        self.assertEqual(Profile.objects.get(user_id=scan.json()["user_id"]).points, 50)

    def test_configured_point_ratio_uses_decimal_floor(self):
        RecyclingPointConfig.objects.update(active=False)
        RecyclingPointConfig.objects.create(grams_per_point="5.00", active=True)
        user = User.objects.create_user(username="ratio@example.com")
        Profile.objects.create(user=user, phone="9876543217", rfid_uid="A3F82C30")
        RFIDCard.objects.create(user=user, uid="A3F82C30", card_id="RF010")
        scan = self.post_json("/api/machines/rfid-scan/", self.machine_payload("rfid_detected", {"rfid_uid": "A3F82C30", "card_id": "RF010", "name": "Ratio User", "phone": "9876543217"}))

        weight = self.post_json("/api/machines/weight/", self.machine_payload("weight_stable", {"weight_g": "18.00"}, session_id=scan.json()["session_id"]))

        self.assertEqual(weight.status_code, 200)
        self.assertEqual(weight.json()["points"], 3)

    def test_exact_session_lifecycle_updates_authenticated_api(self):
        user = User.objects.create_user(username="lifecycle@example.com")
        Profile.objects.create(user=user, phone="9876543218", rfid_uid="A3F82C31")
        RFIDCard.objects.create(user=user, uid="A3F82C31", card_id="RF011")
        self.client.force_login(user)
        start = self.client.post("/api/recycling/start/", data=json.dumps({"machine_id": self.machine.id}), content_type="application/json")
        self.assertEqual(start.status_code, 200)
        self.assertEqual(start.json()["event"], "WAITING_FOR_RFID")
        self.assertEqual(RecyclingSession.objects.count(), 0)

        scan = self.post_json("/api/machines/rfid-scan/", self.machine_payload("rfid_detected", {"rfid_uid": "A3F82C31", "card_id": "RF011", "name": "Lifecycle User", "phone": "9876543218"}))
        session_id = scan.json()["session_id"]
        self.assertEqual(RecyclingSession.objects.filter(user=user).count(), 1)
        active = self.client.get("/api/recycling/active-session/")
        self.assertEqual(active.status_code, 200)
        self.assertEqual(active.json()["session"]["session_id"], session_id)

        progress = self.client.get(f"/api/recycling/session/{session_id}/")
        self.assertEqual(progress.status_code, 200)
        self.assertEqual(progress.json()["session"]["session_id"], session_id)

        weight = self.post_json("/api/machines/weight/", self.machine_payload("weight_stable", {"weight_g": 500}, session_id=session_id))
        self.assertEqual(weight.json()["status"], "MEASURING")
        processing = self.post_json("/api/machines/processing/", self.machine_payload("processing", session_id=session_id))
        self.assertEqual(processing.json()["status"], "PROCESSING")
        complete = self.post_json("/api/machines/session-complete/", self.machine_payload("deposit_completed", session_id=session_id))

        self.assertEqual(complete.json()["status"], "COMPLETED")
        self.assertEqual(complete.json()["weight"], 500.0)
        self.assertEqual(complete.json()["points"], 500)
        self.assertEqual(RecyclingSession.objects.get(session_id=session_id).points, 500)

    def test_session_api_does_not_expose_another_users_session(self):
        owner = User.objects.create_user(username="owner@example.com")
        other_user = User.objects.create_user(username="other@example.com")
        owner_profile = Profile.objects.create(user=owner, phone="9876543219", rfid_uid="A3F82C32")
        card = RFIDCard.objects.create(user=owner, uid=owner_profile.rfid_uid, card_id="RF012")
        session = RecyclingSession.objects.create(user=owner, machine=self.machine, rfid_card=card, rfid_uid=card.uid, status="COMPLETED", weight_grams=500, points=500)
        self.client.force_login(other_user)

        response = self.client.get(f"/api/recycling/session/{session.session_id}/")

        self.assertEqual(response.status_code, 404)

    def test_admin_point_config_saves_when_password_autofill_is_invalid(self):
        admin = User.objects.create_user(username="admin@example.com", password="admin-password")
        Profile.objects.create(user=admin, role="ADMIN")
        self.client.force_login(admin)

        response = self.client.post("/admin-panel/settings/", {
            "admin_name": "Admin",
            "admin_email": "admin@example.com",
            "grams_per_point": "5",
            "new_password": "autofilled-password",
            "password_confirmation": "",
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(RecyclingPointConfig.objects.get(active=True).grams_per_point, 5)

    def test_admin_dashboard_points_and_users_exclude_admin_profile(self):
        admin = User.objects.create_user(username="dashboard-admin@example.com")
        Profile.objects.create(user=admin, role="ADMIN", points=156)
        user = User.objects.create_user(username="dashboard-user@example.com")
        Profile.objects.create(user=user, role="USER", points=99)
        RecyclingSession.objects.create(user=user, machine=self.machine, rfid_uid="DASHBOARD", status="COMPLETED", points=164)
        RecyclingSession.objects.create(user=user, machine=self.machine, rfid_uid="DASHBOARD", status="PROCESSING", points=20)
        self.client.force_login(admin)

        dashboard_response = self.client.get("/admin-dashboard/")
        users_response = self.client.get("/admin-panel/users/")

        self.assertEqual(dashboard_response.context["points"], 99)
        self.assertEqual(dashboard_response.context["users"], 1)
        self.assertEqual(len(users_response.context["users_list"]), 1)
        self.assertEqual(users_response.context["users_list"][0].user_id, user.id)

    def test_admin_report_chart_has_real_distinct_month_values_and_zero_days(self):
        admin = User.objects.create_user(username="chart-admin@example.com")
        Profile.objects.create(user=admin, role="ADMIN")
        user = User.objects.create_user(username="chart-user@example.com")
        session_dates = [timezone.localdate() - timedelta(days=2), timezone.localdate() - timedelta(days=1), timezone.localdate()]
        for session_date, weight in zip(session_dates, [50, 200, 0]):
            session = RecyclingSession.objects.create(user=user, machine=self.machine, rfid_uid="CHART", status="COMPLETED", weight_grams=weight, points=0)
            session.completed_at = timezone.make_aware(timezone.datetime.combine(session_date, timezone.datetime.min.time()))
            session.save(update_fields=["completed_at"])
        self.client.force_login(admin)

        response = self.client.get("/admin-panel/reports/")
        chart_data = response.context["chart_data"]
        values_by_date = {point["date"]: point["grams"] for point in chart_data}

        self.assertEqual(len(chart_data), 31)
        self.assertEqual([values_by_date[session_date.isoformat()] for session_date in session_dates], [50.0, 200.0, 0.0])
        self.assertEqual(sum(point["grams"] for point in chart_data), 250.0)

    def test_phone_conflict_does_not_create_second_user(self):
        response = self.post_json("/api/machines/rfid-scan/", self.machine_payload("rfid_detected", {"rfid_uid": "A3F82C23", "card_id": "RF005", "name": "Unknown User", "phone": "9876543213"}))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(User.objects.count(), 0)

    def test_existing_user_cannot_receive_a_second_different_card(self):
        user = User.objects.create_user(username="existing@example.com", first_name="Existing User")
        Profile.objects.create(user=user, phone="9876543214", rfid_uid="A3F82C24")
        RFIDCard.objects.create(user=user, uid="A3F82C24", card_id="RF006")
        response = self.post_json("/api/machines/rfid-scan/", self.machine_payload("rfid_detected", {"rfid_uid": "A3F82C25", "card_id": "RF007", "name": "Existing User", "phone": "9876543214"}))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(RFIDCard.objects.count(), 1)

    def test_invalid_weight_awards_no_points(self):
        user = User.objects.create_user(username="invalid@example.com", first_name="Invalid Weight")
        Profile.objects.create(user=user, phone="9876543216")
        scan = self.post_json("/api/machines/rfid-scan/", self.machine_payload("rfid_detected", {"rfid_uid": "A3F82C26", "card_id": "RF008", "name": "Invalid Weight", "phone": "9876543216"}))
        response = self.post_json("/api/machines/weight/", self.machine_payload("weight_stable", {"weight_g": 501}, session_id=scan.json()["session_id"]))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(Profile.objects.get(user_id=scan.json()["user_id"]).points, 0)

    def test_completion_before_weight_is_rejected(self):
        user = User.objects.create_user(username="state@example.com", first_name="Wrong State")
        Profile.objects.create(user=user, phone="9876543215")
        scan = self.post_json("/api/machines/rfid-scan/", self.machine_payload("rfid_detected", {"rfid_uid": "A3F82C27", "card_id": "RF009", "name": "Wrong State", "phone": "9876543215"}))
        response = self.post_json("/api/machines/session-complete/", self.machine_payload("deposit_completed", session_id=scan.json()["session_id"]))
        self.assertEqual(response.status_code, 409)

    def test_user_notifications_page_limits_display_and_counts_unread(self):
        user = User.objects.create_user(username="notifications@example.com")
        self.client.force_login(user)
        Notification.objects.bulk_create([
            Notification(user=user, title=f"Notification {index}", message="Message", is_read=index % 2 == 0)
            for index in range(55)
        ])

        response = self.client.get("/notifications/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["notifications"]), 50)
        self.assertEqual(response.context["unread_count"], 27)