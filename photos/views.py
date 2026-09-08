import uuid
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, JsonResponse, Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from bookings.models import Tour
from .models import Album, Photo
from .services import store_photo


def authorized_tour(user, pk):
    tours = Tour.objects.all() if user.is_staff else Tour.objects.filter(responsible=user)
    return get_object_or_404(tours, pk=pk)


@login_required
@require_POST
def upload(request, tour_pk):
    tour = authorized_tour(request.user, tour_pk)
    try:
        uploaded = request.FILES.get("photo")
        if not uploaded:
            raise ValueError("Choose a photo to upload.")
        photo = store_photo(tour, uploaded, uuid.UUID(request.POST.get("upload_id", "")), request.user)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({
        "id": photo.pk, "filename": photo.filename,
        "thumbnail": reverse("photos:asset", args=[photo.album.token, photo.pk, "thumbnail"]),
        "display": reverse("photos:asset", args=[photo.album.token, photo.pk, "display"]),
        "remove": reverse("photos:remove", args=[photo.pk]),
        "width": photo.width, "height": photo.height,
        "url": reverse("photos:album", args=[photo.album.token]),
    })


@login_required
@require_POST
def remove(request, photo_pk):
    photo = get_object_or_404(Photo.objects.select_related("album"), pk=photo_pk)
    authorized_tour(request.user, photo.album.tour_id)
    files = [(field.storage, field.name) for field in [photo.original, photo.display, photo.thumbnail]]
    photo.delete()
    for storage, name in files:
        storage.delete(name)
    return JsonResponse({"ok": True})


@require_GET
def album(request, token):
    item = get_object_or_404(Album.objects.select_related("tour__responsible"), token=token)
    response = render(request, "photos/album.html", {"album": item, "photos": item.photos.all(), "gallery_links": settings.PHOTO_GALLERY_LINKS})
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response["Referrer-Policy"] = "no-referrer"
    response["Cache-Control"] = "private, no-cache"
    return response


@require_GET
def asset(request, token, photo_pk, variant):
    if variant not in {"original", "display", "thumbnail"}:
        raise Http404
    photo = get_object_or_404(Photo, pk=photo_pk, album__token=token)
    field = getattr(photo, variant)
    response = FileResponse(field.open("rb"), as_attachment=variant == "original", filename=photo.filename if variant == "original" else "tour-photo.jpg", content_type="application/octet-stream" if variant == "original" else "image/jpeg")
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Robots-Tag"] = "noindex, noarchive"
    response["Cache-Control"] = "private, max-age=3600"
    return response
