from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("recycling", "0005_profile_rfid_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="phone",
            field=models.CharField(blank=True, max_length=30),
        ),
    ]