from django.db import migrations, models


def create_default_point_config(apps, schema_editor):
    RecyclingPointConfig = apps.get_model("recycling", "RecyclingPointConfig")
    RecyclingPointConfig.objects.get_or_create(grams_per_point=1, active=True)


class Migration(migrations.Migration):
    dependencies = [("recycling", "0010_rfidcard_session_card")]

    operations = [
        migrations.CreateModel(
            name="RecyclingPointConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("grams_per_point", models.DecimalField(decimal_places=2, default=1, max_digits=8)),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.RunPython(create_default_point_config, migrations.RunPython.noop),
    ]