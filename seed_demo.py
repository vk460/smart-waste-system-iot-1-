import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','ecoreward.settings')
import django
django.setup()
from django.contrib.auth.models import User
from recycling.models import Profile, Machine, RecyclingPointConfig, Reward
user, created = User.objects.get_or_create(username='prajwal', defaults={'first_name':'Prajwal','email':'prajwal@example.com'})
if created: user.set_password('ecoreward123'); user.save()
Profile.objects.get_or_create(user=user, defaults={'rfid_uid':'A3:B7:91:24','points':156})
admin, created = User.objects.get_or_create(username='admin', defaults={'email':'admin@example.com','is_staff':True,'is_superuser':True})
if created: admin.set_password('admin123'); admin.save()
Profile.objects.get_or_create(user=admin, defaults={'role':'ADMIN'})
for code, level in [('MACHINE_001',62),('MACHINE_002',41),('MACHINE_003',89)]: Machine.objects.get_or_create(code=code, defaults={'bin_level':level,'status':'OFFLINE' if code=='MACHINE_003' else 'ONLINE','api_key':'change-me'})
demo_rewards = [
	('Eco Water Bottle', 'Reusable bottle made from recycled material.', 'Drinkware', 500, 25, '/static/images/eco-bottle.webp'),
	('Hot Water Thermos', 'Insulated thermos for long-lasting temperature retention.', 'Drinkware', 800, 20, '/static/images/hot flask.webp'),
	('Electric Kettle', 'Compact electric kettle for fast, convenient boiling.', 'Kitchen', 1200, 10, '/static/images/electric kettle.jpg'),
	('Food Steamer', 'Practical steamer for healthier everyday cooking.', 'Kitchen', 1500, 8, '/static/images/food steamer.png'),
	('Stainless Steel Bottle', 'Durable stainless steel bottle for daily use.', 'Drinkware', 700, 18, '/static/images/stainless steel bottle.png'),
	('Eco Travel Mug', 'Reusable travel mug for drinks on the go.', 'Lifestyle', 600, 16, '/static/images/llifestyle -mug.jpg'),
]
for name, description, category, points, stock, image_url in demo_rewards:
	Reward.objects.get_or_create(name=name, defaults={'description':description,'category':category,'image_url':image_url,'points_required':points,'stock':stock})
Reward.objects.get_or_create(name='₹50 Green Coupon', defaults={'description':'A little reward for a big planet.','category':'Lifestyle','points_required':250,'stock':50})
RecyclingPointConfig.objects.get_or_create(grams_per_point=1, active=True)
print('Demo data ready: prajwal/ecoreward123 and admin/admin123')
