from background_task import background


@background(schedule=0)
def publishing_checks():
    """Every 15 minutes (idempotent): pre-publish checks and alerts, late manual-post reminders."""
    from apps.publishing.checks import run_checks
    from apps.publishing.services import send_reminders

    run_checks()
    send_reminders()
