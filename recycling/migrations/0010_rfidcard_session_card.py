from django.db import migrations, models
import django.db.models.deletion


def copy_existing_rfid_cards(apps, schema_editor):
    Profile = apps.get_model("recycling", "Profile")
    RFIDCard = apps.get_model("recycling", "RFIDCard")
    for profile in Profile.objects.exclude(rfid_uid__isnull=True).exclude(rfid_uid=""):
        RFIDCard.objects.get_or_create(uid=profile.rfid_uid, defaults={"user_id": profile.user_id})


class Migration(migrations.Migration):
    dependencies = [("recycling", "0009_reward_image")]

    operations = [
        migrations.CreateModel(
            name="RFIDCard",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("uid", models.CharField(max_length=64, unique=True)),
                ("card_id", models.CharField(blank=True, max_length=64, null=True, unique=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="rfid_cards", to="auth.user")),
            ],
        ),
        migrations.AddField(
            model_name="recyclingsession",
            name="rfid_card",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="sessions", to="recycling.rfidcard"),
        ),
        migrations.RunPython(copy_existing_rfid_cards, migrations.RunPython.noop),
    ]