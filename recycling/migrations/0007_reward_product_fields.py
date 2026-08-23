import uuid

from django.db import migrations, models

from recycling.models import make_transaction_id


def populate_transaction_ids(apps, schema_editor):
    Redemption = apps.get_model("recycling", "Redemption")
    for redemption in Redemption.objects.filter(transaction_id__isnull=True):
        redemption.transaction_id = f"TXN-{uuid.uuid4().hex[:8].upper()}"
        redemption.save(update_fields=["transaction_id"])


class Migration(migrations.Migration):
    dependencies = [
        ("recycling", "0006_profile_phone"),
    ]

    operations = [
        migrations.AddField(
            model_name="reward",
            name="category",
            field=models.CharField(default="Eco Products", max_length=40),
        ),
        migrations.AddField(
            model_name="reward",
            name="image_url",
            field=models.URLField(blank=True),
        ),
        migrations.AddField(
            model_name="redemption",
            name="status",
            field=models.CharField(default="REDEEMED", max_length=20),
        ),
        migrations.AddField(
            model_name="redemption",
            name="transaction_id",
            field=models.CharField(max_length=24, null=True, unique=True),
        ),
        migrations.RunPython(populate_transaction_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="redemption",
            name="transaction_id",
            field=models.CharField(default=make_transaction_id, max_length=24, unique=True),
        ),
    ]