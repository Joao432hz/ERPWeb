# Generated manually for ERPWeb – Finance hardening
# Objetivo: reforzar reglas de negocio a nivel DB (vendible, profesional)

from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0002_alter_financialmovement_status"),
    ]

    operations = [

        # -------------------------------------------------
        # 1) amount nunca negativo
        # -------------------------------------------------
        migrations.AddConstraint(
            model_name="financialmovement",
            constraint=models.CheckConstraint(
                check=Q(amount__gte=0),
                name="fin_mov_amount_gte_0",
            ),
        ),

        # -------------------------------------------------
        # 2) status = PAID => paid_at IS NOT NULL
        # -------------------------------------------------
        migrations.AddConstraint(
            model_name="financialmovement",
            constraint=models.CheckConstraint(
                check=(
                    Q(status="PAID", paid_at__isnull=False)
                    | ~Q(status="PAID")
                ),
                name="fin_mov_paid_requires_paid_at",
            ),
        ),

        # -------------------------------------------------
        # 3) status != PAID => paid_at IS NULL
        # (OPEN o VOID no deben tener paid_at)
        # -------------------------------------------------
        migrations.AddConstraint(
            model_name="financialmovement",
            constraint=models.CheckConstraint(
                check=(
                    Q(status="PAID")
                    | Q(paid_at__isnull=True)
                ),
                name="fin_mov_non_paid_no_paid_at",
            ),
        ),
    ]
