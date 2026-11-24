from django.urls import path

from . import portal

app_name = "reviews_portal"

urlpatterns = [
    path("", portal.batch, name="batch"),
    path("approve/", portal.approve_batch, name="approve_batch"),
    path("posts/<uuid:post_id>/decide/", portal.decide, name="decide"),
    path("posts/<uuid:post_id>/comment/", portal.comment, name="comment"),
]
