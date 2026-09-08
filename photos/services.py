import io
import uuid
import warnings
from pathlib import Path
from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener
from django.core.files.base import ContentFile
from django.db import transaction, IntegrityError
from .models import Album, Photo

register_heif_opener()
Image.MAX_IMAGE_PIXELS = 60_000_000
MAX_BYTES = 50 * 1024 * 1024


def store_photo(tour, uploaded, upload_id, user):
    if uploaded.size > MAX_BYTES:
        raise ValueError("Each photo must be 50 MB or smaller.")
    album, _ = Album.objects.get_or_create(tour=tour)
    existing = album.photos.filter(upload_id=upload_id).first()
    if existing:
        return existing
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(uploaded) as source:
                if source.format not in {"JPEG", "PNG", "WEBP", "HEIF", "HEIC"} or getattr(source, "is_animated", False):
                    raise ValueError("Choose still JPEG, PNG, WebP, or HEIC photos.")
                source.load()
                picture = ImageOps.exif_transpose(source).convert("RGB")
                # Full pixel dimensions retained; guest viewing copies omit location/EXIF data.
                picture.info.clear()
                display = io.BytesIO()
                picture.save(display, format="JPEG", quality=95, optimize=True)
                width, height = picture.size
                picture.thumbnail((600, 600))
                thumb = io.BytesIO()
                picture.save(thumb, format="JPEG", quality=85)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("This photo could not be read, or exceeds the 60-megapixel limit.") from exc
    uploaded.seek(0)
    photo = Photo(album=album, upload_id=upload_id, uploaded_by=user, filename=Path(uploaded.name).name[:240], width=width, height=height)
    stored = []
    try:
        with transaction.atomic():
            Album.objects.select_for_update().get(pk=album.pk)
            if album.photos.count() >= 100:
                raise ValueError("This departure already has 100 photos.")
            name = uuid.uuid4().hex
            for field, filename, content in [(photo.original, name + ".original", uploaded), (photo.display, name + ".jpg", ContentFile(display.getvalue())), (photo.thumbnail, name + ".jpg", ContentFile(thumb.getvalue()))]:
                field.save(filename, content, save=False)
                stored.append(field)
            photo.save()
    except Exception as exc:
        for field in stored:
            field.storage.delete(field.name)
        if isinstance(exc, IntegrityError):
            existing = album.photos.filter(upload_id=upload_id).first()
            if existing:
                return existing
        raise
    return photo
