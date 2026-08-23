from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("recycling", "0008_populate_reward_images")]

    operations = [
        migrations.AddField(
            model_name="reward",
            name="image",
            field=models.FileField(blank=True, upload_to="reward_images/"),
        ),
    ]