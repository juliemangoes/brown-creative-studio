from background_task import background


@background(schedule=0)
def research_weekly():
    """Hourly (idempotent): the weekly brief for each active client on the chosen day, and outcome checks."""
    from apps.research.services import run_weekly

    run_weekly()
