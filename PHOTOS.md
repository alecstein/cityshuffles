# Departure photo galleries

My Tours shows upload controls and a thumbnail grid below the group-message history. Each scheduled Tour owns one album; its unguessable UUID URL is stable as photos are added or removed. The guest page is rendered from current album records, not a separately deployed website. Anyone holding the link can view/download photos; no guest login is required.

Original bytes are retained for downloads, including original metadata. Full-pixel-size JPEG viewing copies and 600px thumbnails omit EXIF/GPS. HEIC/HEIF originals are supported with pillow-heif, and get JPEG viewing copies for browser compatibility. Still JPEG, PNG and WebP also work. Limits: 50 MB/file, 60 megapixels, 100 photos/departure.

The browser uploads two photos concurrently and warns before leaving during upload. Successful photos appear immediately in the gallery and enable Send photos, even if another file fails. Upload errors are shown individually; there are no retry or keep-successful controls. Successful uploads survive page closure; unfinished uploads require selecting the files again. No background/locked-phone guarantee, client resizing, or automatic message send.

The Photos action uses the protected Photos template and existing per-recipient delivery tracking/retries, excluding canceled bookings. `{photos_url}` is snapshotted into text/email messages. The action is deduplicated per departure. Adding images updates the same gallery without sending another blast. A public HTTPS `PUBLIC_BASE_URL` is required for sends to real guests; local demo galleries remain previewable.

Files currently use Django's default filesystem storage in `.photo-storage/`, excluded from source control. Do not serve that directory directly as public media. Token-checked views serve files and force attachment downloads for originals. Back up both the database and this directory; production requires persistent storage, web-server upload/time limits aligned with these limits, and preferably private object storage for scale. No storage provider or public hosting was provisioned.

Set `PHOTO_GALLERY_LINKS` in settings to a list of `{label, url}` objects when the company links are ready. Placeholder labels appear until then. Use trusted HTTPS URLs. The gallery excludes guest contact information and is marked noindex; the token is access control by possession, not individual guest authorization.

The circular × immediately deletes that photo's files without confirmation; it also disappears from existing album links. Full-resolution downloads can contain original location metadata.
