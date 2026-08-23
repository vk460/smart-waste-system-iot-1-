from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [("recycling", "0012_recyclingpointconfig_one_active")]

    operations = [
        migrations.RemoveConstraint(
            model_name="recyclingpointconfig",
            name="one_active_recycling_point_config",
        ),
        migrations.AddField(
            model_name="recyclingpointconfig",
            name="config_key",
            field=models.PositiveSmallIntegerField(default=1),
        ),
        migrations.AddConstraint(
            model_name="recyclingpointconfig",
            constraint=models.UniqueConstraint(
                condition=Q(active=True),
                fields=("config_key",),
                name="one_active_recycling_point_config",
            ),
        ),
    ]