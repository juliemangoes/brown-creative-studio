from background_task import background


@background(schedule=0)
def billing_daily():
    """Hourly (idempotent): overdue statuses, payment reminders, next month's retainer drafts."""
    from apps.billing.services import run_daily

    run_daily()
