from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [("recycling", "0011_recyclingpointconfig")]

    operations = [
        migrations.AddConstraint(
            model_name="recyclingpointconfig",
            constraint=models.UniqueConstraint(
                condition=Q(active=True),
                fields=("active",),
                name="one_active_recycling_point_config",
            ),
        ),
    ]