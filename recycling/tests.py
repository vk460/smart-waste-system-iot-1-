import json

from django.contrib.auth.models import User
from django.test import TestCase

from .models import Machine, Profile, RFIDCard, RecyclingSession, RewardRule


class RFIDMachineFlowTests(TestCase):
    def setUp(self):
        self.machine = Machine.objects.create(code="MACHINE_TEST", api_key="test-key", status="ONLINE")
        RewardRule.objects.create(minimum_grams=0, maximum_grams=500, points=60)
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
        self.assertEqual(body["status"], "verified")
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
        self.assertEqual(RecyclingSession.objects.count(), 2)

    def test_weight_belongs_to_card_session_and_completion_is_idempotent(self):
        user = User.objects.create_user(username="asha@example.com", first_name="Asha Rao")
        Profile.objects.create(user=user, phone="9876543212")
        scan = self.post_json("/api/machines/rfid-scan/", self.machine_payload("rfid_detected", {"rfid_uid": "A3F82C21", "card_id": "RF003", "name": "Asha Rao", "phone": "9876543212"}))
        session_id = scan.json()["session_id"]
        weight = self.post_json("/api/machines/weight/", self.machine_payload("weight_stable", {"weight_g": 50}, session_id=session_id))
        self.assertEqual(weight.json()["user_id"], scan.json()["user_id"])
        self.assertEqual(weight.json()["points"], 60)
        complete = self.post_json("/api/machines/session-complete/", self.machine_payload("deposit_completed", session_id=session_id))
        retry = self.post_json("/api/machines/session-complete/", self.machine_payload("deposit_completed", session_id=session_id))
        self.assertEqual(complete.status_code, 200)
        self.assertTrue(retry.json()["idempotent"])
        self.assertEqual(Profile.objects.get(user_id=scan.json()["user_id"]).points, 60)

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