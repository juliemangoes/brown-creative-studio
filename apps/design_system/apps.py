from django.apps import AppConfig


class DesignSystemConfig(AppConfig):
    """Brown Creative Studio design tokens: the template codemod, its guards, and the style guide page."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.design_system"
    label = "design_system"
    verbose_name = "Design system"
