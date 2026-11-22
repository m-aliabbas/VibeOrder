from django.db import migrations


class Migration(migrations.Migration):
    """Everyone signs in with username + password; device pairing codes are gone."""

    dependencies = [("core", "0002_branch_address_branch_discount_limit_bp_and_more")]

    operations = [
        migrations.RenameField("staff", "pin_hash", "password_hash"),
        migrations.DeleteModel("PairingCode"),
    ]
