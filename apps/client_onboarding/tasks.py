from background_task import background


@background(schedule=0)
def send_onboarding_reminders():
    """Hourly: email clients whose onboarding has a reminder due (apps.client_onboarding.services)."""
    from apps.client_onboarding.services import send_reminders

    send_reminders()
