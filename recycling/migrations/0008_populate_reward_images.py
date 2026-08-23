from django.db import migrations


REWARD_IMAGES = {
    "Eco Water Bottle": "/static/images/eco-bottle.webp",
    "Hot Water Thermos": "/static/images/hot flask.webp",
    "Electric Kettle": "/static/images/electric kettle.jpg",
    "Food Steamer": "/static/images/food steamer.png",
    "Stainless Steel Bottle": "/static/images/stainless steel bottle.png",
    "Eco Travel Mug": "/static/images/llifestyle -mug.jpg",
}


def populate_reward_images(apps, schema_editor):
    Reward = apps.get_model("recycling", "Reward")
    for name, image_url in REWARD_IMAGES.items():
        Reward.objects.filter(name=name, image_url="").update(image_url=image_url)


class Migration(migrations.Migration):
    dependencies = [("recycling", "0007_reward_product_fields")]
    operations = [migrations.RunPython(populate_reward_images, migrations.RunPython.noop)]