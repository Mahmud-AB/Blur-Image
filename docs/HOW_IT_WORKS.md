# BLUR_IMAGE — Technical Documentation

This document explains what the project does, how each piece works, and which technologies, algorithms, and techniques are used — and why.

---

## 1. What this project is

**BLUR_IMAGE** is a web app for **privacy annotation**: upload street / dataset images, draw polygons or lines over sensitive regions (signboards, number plates) or label regions that stay sharp (roads, obstacles), **Gaussian-blur** only the privacy regions, save the file, and export annotation coordinates to Excel.

Typical workflow:

1. Sign up / log in.
2. Upload images or a whole folder.
3. Click points on the preview to draw shapes; assign a category.
4. **Blur & save** — server blurs privacy categories only (`signboard`, `number_plate`); `road` and `obstacle` are stored as annotations without blurring. Downloads the result.
5. Optionally restore the original, bulk-download edited images as a ZIP, or export coordinates as `.xlsx`.

It is a **Django + Pillow** backend with a **single-page editor**: markup in `templates/app/home.html`, editor logic in `app/static/app/editor.js` (vanilla JS, Canvas + SVG). `test.py` is a standalone prototype of the same blur idea.

---

## 2. Repository layout

| Path | Role |
|------|------|
| `manage.py` | Django CLI entry (`runserver`, `migrate`, `test`). |
| `requirements.txt` | Runtime deps: Django 6, Pillow, openpyxl. |
| `core/` | Project config: settings, root URLs, WSGI/ASGI. |
| `app/` | Application: models, views, imaging, URLs, admin, tests. |
| `app/imaging.py` | Pillow helpers: EXIF, encode, thumbnails, regional blur. |
| `app/views.py` | HTTP layer: auth, upload, edit, restore, serve, export. |
| `templates/app/home.html` | Page markup + auth tabs + `window.BLUR_EDITOR` config. |
| `app/static/app/editor.js` | Gallery, canvas editor, zoom, download folder, blur/restore. |
| `media/uploads/` | Current working copies (original or edited). |
| `media/original_uploads/` | Immutable originals for restore. |
| `media/thumbs/` | Small JPEG gallery thumbnails (max 216 px). |
| `test.py` | Offline Pillow prototype (no Django). |
| `app/tests.py` | Django test suite for upload / blur / restore / Excel / thumbs. |

---

## 3. High-level architecture

```
Browser (home.html + editor.js)
  │  Canvas preview + SVG overlay (click points)
  │  fetch() JSON / FormData + CSRF
  ▼
Django views (app/views.py)
  │  Auth, upload, edit, restore, serve, export
  ▼
imaging.py ──► regional Gaussian blur + mask composite + thumbs
SQLite      ──► users, ImageUpload, AnnotationRow
Disk        ──► media/uploads + original_uploads + thumbs
openpyxl    ──► annotations.xlsx
```

**Why this split**

- Blur must run on **full-resolution pixels** on the server. The browser only collects coordinates and shows a preview; it does not re-encode the master file.
- Pillow work lives in `imaging.py` so views stay as request/response code.
- Keeping originals on disk lets the user undo blur without re-uploading.
- Thumbnails keep the gallery light; the editor still loads the full file.
- Annotations are stored separately from pixels so they can be exported for dataset / ML labeling.

---

## 4. Technology stack (and why)

| Technology | Where | Why |
|------------|--------|-----|
| **Python 3 + Django 6** | Backend | Auth, sessions, CSRF, file uploads, admin, URL routing without building a custom server. |
| **SQLite** | `db.sqlite3` | Zero-ops local DB. Fine for a single-machine annotation tool. |
| **Pillow (PIL)** | `app/imaging.py` | Pure-Python-friendly imaging. Gaussian blur, masks, EXIF, JPEG/PNG/WebP — no OpenCV/native build pain on Windows. |
| **openpyxl** | Excel export | Writes `.xlsx` with one sheet per category. Standard for sharing labels with non-developers. |
| **Django FileField + MEDIA_ROOT** | Storage | Simple disk storage; working copy, original, and thumbnail stay as real files. |
| **Django FileResponse** | `serve_image`, ZIP download | Streams bytes from disk / a spooled temp file instead of buffering the whole payload in a string. |
| **Django session auth** | Login | Username/password, `create_user` + `authenticate`/`login`/`logout`. Enough for a private tool. |
| **Vanilla JavaScript** | `editor.js` | No React/build step. Canvas for pixels, SVG for overlay. |
| **HTML Canvas 2D** | Preview | Draws the image at **natural width × height** so click coordinates match server pixels 1:1. |
| **SVG overlay** | Shapes | Resolution-independent dots, polygons, labels on top of the canvas. `pointer-events: none` so clicks hit the canvas. |
| **Tailwind CDN** | Styling | Fast dark UI without a frontend toolchain. |
| **File System Access API** | Download folder | Chrome/Edge: write blurred files straight into a chosen folder. |
| **IndexedDB** | Persist folder handle | Directory handles cannot go in `localStorage`; IndexedDB can store them per user id. |
| **ZIP (zlib DEFLATE)** | Bulk download | One archive of all edited images (`SpooledTemporaryFile`). |
| **WSGI / ASGI** | Deploy hooks | Standard Django entry points (`core/wsgi.py`, `core/asgi.py`). |

**Why not OpenCV / NumPy blur?**  
Pillow’s `GaussianBlur` + `Image.composite` is enough. Fewer native dependencies, easier install, and the mask technique already gives correct region edges.

**Why not client-side blur only?**  
Browser canvas would downscale or re-encode (JPEG quality loss). The product goal is **full-resolution, format-preserving** output for datasets.

**Upload size settings** (`core/settings.py`)

- `FILE_UPLOAD_MAX_MEMORY_SIZE = 50MB` — large photos stay in memory rather than temp files.
- `DATA_UPLOAD_MAX_MEMORY_SIZE = 500MB` — folder uploads of many images.

Templates load from `BASE_DIR / "templates"`. Static editor JS is served from the `app` package (`app/static/app/editor.js`).

---

## 5. Algorithms and techniques (and why)

### 5.1 Selective Gaussian blur via padded crop + mask

This is the core algorithm, implemented as `blur_shapes` in `app/imaging.py` and called from `edit_image`. `test.py` is a simpler full-frame prototype of the same mask idea.

Only shapes in blur categories are passed in. `road` and `obstacle` are excluded via `NO_BLUR_CATEGORIES` in `edit_image` — their coordinates are still saved as `AnnotationRow`s, but those pixels are left unchanged. If every shape is a no-blur category, the image file is not rewritten.

**Steps**

1. Open the source bytes; apply EXIF orientation.
2. Convert color mode for the output format (JPEG → RGB, PNG may stay RGBA).
3. Compute the **union bounding box** of all shapes, expanded by `BLUR_PAD = ceil(22 × 3)` so the Gaussian kernel has enough neighbors.
4. Crop that box; blur **only the crop**: `ImageFilter.GaussianBlur(radius=22)`.
5. Build a single-channel mask (`mode "L"`), same size as the crop, filled with `0` (black = keep original).
6. Draw each user shape in **white (`255`)** on the mask, shifted into crop coordinates:
   - **≥ 3 points** → filled polygon.
   - **exactly 2 points** → thick line (stroke width `max(8, min(w,h)//40)`).
7. Composite: `Image.composite(blurred_crop, crop, mask)`, paste back onto the full working image.
8. Save with format-specific options (JPEG quality 100, WebP lossless, PNG compress).

**Why pad the crop instead of blurring the whole image?**

A Gaussian kernel needs **neighbor pixels**. Cropping *exactly* to the polygon and blurring that patch would look wrong at the boundary (hard seam / incomplete kernel). Padding by ~3× radius keeps correct edges while avoiding a full-frame blur on a 12MP street photo when only a plate is annotated.

If the bbox cannot be computed, the code falls back to the full image.

**Why Gaussian (not pixelate / box blur / black box)?**

- Looks more natural for privacy (plates, signs).
- Radius `22` is strong enough to destroy readability at typical photo resolutions without turning the patch into a solid smear.
- Pillow implements it efficiently; no extra library.

**Why `Image.composite` then `paste`?**

`composite(blurred, original, mask)` is explicit: white → blurred, black → original. The result is pasted at the crop origin. Same idea as `test.py`’s `image.paste(blurred, mask=mask)`, but only over the padded region.

### 5.2 Polygon winding order (centroid + `atan2`)

Users click corners in **any order**. Filling an unordered polygon can self-intersect (bow-tie).

**Algorithm** (`order_points_clockwise` / `orderPointsClockwise`):

1. Compute centroid \((c_x, c_y)\).
2. Sort vertices by polar angle \(\operatorname{atan2}(y - c_y, x - c_x)\).

That yields a consistent clockwise/counter-clockwise boundary so `draw.polygon` / SVG `<polygon>` fill the intended region.

Applied when there are **≥ 3** points (backend: ≥ 4 for reordering in `order_points_clockwise`; frontend closes at ≥ 4 similarly, ≥ 3 still draws).

### 5.3 Coordinate spaces: display vs. natural pixels

The canvas bitmap is set to **`naturalWidth × naturalHeight`**. CSS zoom only changes *display* size.

`getCanvasPoint`:

```
x = (clientX - rect.left) * (displayWidth / cssWidth)
```

Because `displayWidth === naturalWidth`, clicks are in **image pixel coordinates**. The server uses those numbers directly. No extra scale factor.

**Why:** annotation datasets need pixel-accurate boxes/polygons, not CSS-pixel guesses.

### 5.4 EXIF orientation

Phones store rotation in EXIF tag `0x0112`. Browsers often display the photo rotated; raw Pillow pixels may still be sideways.

- `ImageOps.exif_transpose` on open
- `_needs_exif_transpose` → only re-encode on upload if orientation ≠ 1

**Why:** otherwise click coordinates (browser-rotated) would not match server pixels.

On upload, if no transpose is needed, **original bytes are kept unchanged** (no re-compression).

### 5.5 Format-preserving encode

| Format | Save strategy | Why |
|--------|----------------|-----|
| JPEG | quality 100, subsampling 0 (4:4:4), keep EXIF/ICC/DPI | Avoid chroma loss and metadata strip after blur. |
| WebP | lossless, method 6 | Keep quality; method 6 = slower, smaller. |
| PNG | compress_level 3 | Balance size vs. CPU; keep alpha when present. |

`prepare_working_image` converts modes so JPEG never gets an alpha channel (invalid) while PNG/WebP can keep transparency.

### 5.6 Working copy, original, and thumbnail

Each `ImageUpload` has:

- `image` — what the editor loads (may be blurred).
- `original_image` — backup created at upload (or lazily via `ensure_original_backup`).
- `thumbnail` — JPEG ≤ 216 px for the gallery (`media/thumbs/`).

**Edit** writes a new `edited_{id}.*` over `image`, regenerates the thumbnail, and sets `is_edited=True`.  
**Restore** copies `original_image` bytes back onto `image` and rebuilds the thumbnail.

**Why:** non-destructive editing; blur can be stacked on the *current* working copy if already edited (`source = image.image if image.is_edited else image.original_image`).

Gallery `<img>` tags request `GET /image/<id>/?thumb=1`. Missing thumbs are generated lazily (`ensure_thumbnail`). The editor always fetches the full file (no `thumb` query).

### 5.7 Annotation serialization

Per user + category + image name (unique together):

```
shape1: x,y;x,y;x,y
shapes joined by |
example: 0,0;10,0;10,10|20,20;30,20;30,30
```

Integers stay integers; non-integers use 2 decimal places.

**Why text, not JSON column?** Simple to dump into Excel one shape per row. `update_or_create` replaces the row when the same image/category is blurred again.

### 5.8 Click vs. double-click (debounce)

A 280 ms timer distinguishes:

- **Single click** → add a vertex.
- **Double-click** → close the current shape and start a new one.

**Why:** HTML fires click then dblclick; without debounce, double-click would also add an extra point.

### 5.9 Zoom (CSS scale + scroll anchoring)

Zoom is **CSS width/height** of the canvas (1×–4×, step 0.25), not a second resample of the bitmap.

Wheel / Alt+click zooms toward the cursor by adjusting `scrollLeft`/`scrollTop` so the same image point stays under the pointer.

Overlay stroke/dot sizes scale with `viewBoxUnitsPerScreenPixel()` so handles stay readable when zoomed.

### 5.10 Access control

Images are **not** exposed via public `/media/` URLs (`core/urls.py` does not mount `MEDIA_ROOT`). The editor and gallery go through `serve_image`, which requires a logged-in owner (`ImageUpload.user`).

Delete removes the row and all three files (working, original, thumbnail) via `_purge_image`. There is no multi-user share table; each upload belongs to one user.

---

## 6. Data model

### `ImageUpload`

| Field | Meaning |
|-------|---------|
| `user` | Owner (`ForeignKey`, `related_name="images"`). Nullable only for legacy rows. |
| `image` | Working file under `media/uploads/`. |
| `original_image` | Backup under `media/original_uploads/`. |
| `thumbnail` | Gallery JPEG under `media/thumbs/`. |
| `original_name` | Client filename (used for download names and annotation keys). |
| `is_edited` | True after at least one successful blur. |
| `created_at` | Newest first in gallery. |

Migration `0009` copied owners from the old `UserImages` M2M wrapper, then dropped that model.

### `AnnotationCategory`

Fixed choices: `signboard`, `road`, `number_plate`, `obstacle`.

`NO_BLUR_CATEGORIES` = `{road, obstacle}` — these are labeled and exported, but never passed to `blur_shapes`.

### `AnnotationRow`

| Field | Meaning |
|-------|---------|
| `user` | Who drew the labels. |
| `category` | One of the four types. |
| `image_name` | Basename of `original_name`. |
| `coordinate_text` | Serialized shapes (see §5.7). |
| `updated_at` | Last blur that wrote this row. |

Unique: `(user, category, image_name)`.

---

## 7. How a blur request is done (end-to-end)

```
User clicks vertices on canvas
        │
        ▼
Double-click finalizes a shape {points, category}
        │
        ▼
Blur & save → group shapes by category
        │
POST /edit/<id>/  { "annotations": [ {category, shapes: [[{x,y},...]]} ] }
        │
ensure_original_backup
_parse_edit_payload (also accepts legacy {points} / {shapes, category})
filter out NO_BLUR_CATEGORIES (road, obstacle) → shapes to blur
if any blur shapes:
  read original (or current edited) bytes
  blur_shapes → padded crop + GaussianBlur(22) + mask composite
  encode JPEG/PNG/WebP, save edited file + thumbnail, is_edited=True
update_or_create AnnotationRow per category (including road / obstacle)
        │
JSON { id, name, url, is_edited }
        │
Browser downloads file (File System Access or <a download>),
then deletes the gallery card and selects the next image
```

After a successful save the UI **does not** reload the preview. It downloads the file and **removes** the card (`deleteImage`) so the operator can walk through a folder like a queue.

---

## 8. URL map

| Method | Path | View |
|--------|------|------|
| GET | `/` | `home` |
| POST | `/signup/` | `signup` |
| POST | `/login/` | `login_user` |
| POST | `/logout/` | `logout_user` |
| POST | `/upload/` | `upload_images` |
| GET | `/image/<id>/` | `serve_image` (`?download=1` → attachment, `?thumb=1` → gallery JPEG) |
| POST | `/edit/<id>/` | `edit_image` |
| POST | `/restore/<id>/` | `restore_image` |
| POST | `/delete/<id>/` | `delete_image` |
| GET | `/download-updated/` | `download_all_updated_images` |
| POST | `/delete-updated/` | `delete_all_updated_images` |
| GET | `/download-annotations/` | `download_annotations` (`?image_id=` optional) |
| — | `/admin/` | Django admin |

---

## 9. Backend function reference

Constants in `app/imaging.py`: `BLUR_RADIUS = 22`, `BLUR_PAD = ceil(66)`, `THUMB_MAX = 216`, JPEG/WebP quality `100`, default suffix `.png`.

### Image I/O (`app/imaging.py`)

| Function | What it does |
|----------|----------------|
| `file_suffix(filename)` | Returns file extension or `.png`. |
| `output_format(filename)` | Maps extension → Pillow format name (`JPEG` / `WEBP` / `PNG`). |
| `guess_content_type(filename)` | MIME type for `FileResponse`. |
| `read_upload_bytes(uploaded_file)` | Reads all chunks; errors if size mismatch. |
| `read_field_bytes(field)` | Opens a `FileField`, reads bytes, closes. |
| `_needs_exif_transpose(image)` | True if EXIF orientation is set and not `1`. |
| `_save_options(source, format_name)` | Format-specific encode flags + ICC/DPI/EXIF (see §5.5). |
| `encode_image(image, format_name, metadata_source)` | Saves image to bytes with `_save_options`. |
| `prepare_working_image(image, format_name)` | Converts color mode so the format is valid (no alpha on JPEG; keep transparency on PNG/WebP). |
| `normalize_upload_bytes(file_bytes, original_name)` | Opens upload; if EXIF rotate needed, re-encodes; else returns original bytes + dimensions. |
| `make_thumbnail_bytes(file_bytes)` | EXIF-aware resize to max 216 px JPEG (quality 82). |
| `save_thumbnail(image_upload, file_bytes)` | Replaces `thumbnail` FileField. |
| `ensure_thumbnail(image_upload)` | Generates a thumb if missing (used by `?thumb=1`). |
| `order_points_clockwise(points)` | Polar-angle sort around centroid (§5.2). |
| `draw_shape_on_mask(draw, points, image_size)` | Polygon fill or thick line on the blur mask. |
| `union_padded_bbox(shapes, size, pad)` | Axis-aligned union of shapes, clamped and padded. |
| `blur_shapes(source_bytes, original_name, shapes)` | Regional blur + composite + encode (see §5.1). |

### Geometry / annotations / helpers (`app/views.py`)

| Function | What it does |
|----------|----------------|
| `_auth_redirect(message)` | Redirect home with URL-encoded `?auth_error=`. |
| `_require_auth_json(request)` | Returns 401 JSON if anonymous; else `None`. |
| `_user_images(user, edited=None)` | `ImageUpload` queryset for that owner; optional `is_edited` filter. |
| `_image_url(image)` | Reverse URL for `serve_image`. |
| `_image_payload(image, width, height, file_size)` | JSON dict for the frontend gallery. |
| `_purge_image(image)` | Deletes working, original, and thumbnail files, then the row. |
| `ensure_original_backup(image)` | If `original_image` missing, copy current `image` bytes into it. |
| `_normalize_points(points)` | Parses `{x,y}` dicts to `(float, float)` tuples; `None` if invalid. |
| `_normalize_category(raw)` | Lowercases and checks `AnnotationCategory` values. |
| `_format_coord(value)` | Integer string or 2 decimal places. |
| `_save_annotation_rows(image, user, category, shapes)` | Serializes shapes to text; `update_or_create` `AnnotationRow`. |
| `_parse_edit_payload(payload)` | Accepts `{annotations:[...]}` or legacy `{points}` / `{shapes, category}`. Returns normalized shapes + per-category map. |
| `_safe_sheet_name(name)` | Excel sheet names: strip `[]:*?/\\`, max 31 chars. |
| `_annotation_shape_texts(coordinate_text)` | Split `\|` into one string per shape. |
| `_annotations_xlsx_bytes(rows)` | Workbook: one sheet per category, columns Image Name / Coordinates. |

### HTTP views

| Function | What it does |
|----------|----------------|
| `home` | Renders gallery + editor; sets CSRF cookie. Loads only `id`, `original_name`, `is_edited`. Anonymous users see login/signup. |
| `signup` | Creates active user, logs in, redirects home. Errors via `?auth_error=`. |
| `login_user` | Session login or redirect with error. |
| `logout_user` | Logs out, redirect home. |
| `serve_image` | Auth + ownership check; `FileResponse` stream; `Cache-Control: no-store`; `?thumb=1` / `?download=1`. |
| `download_all_updated_images` | ZIP of all edited images (`ZIP_DEFLATED`, unique filenames). |
| `download_annotations` | All user annotations, or one image if `?image_id=`. |
| `delete_all_updated_images` | `_purge_image` on every edited image owned by the user. |
| `upload_images` | Multi-file upload, EXIF normalize, save working + original + thumbnail, return JSON list. |
| `delete_image` | `_purge_image` for that owner’s file. |
| `edit_image` | Validate annotations, blur only non-`NO_BLUR` shapes, save edited file + thumb when needed, persist `AnnotationRow`s. |
| `restore_image` | Replace working file with original bytes; rebuild thumb; `is_edited=False`. |

### Other Python modules

| Symbol | File | What it does |
|--------|------|----------------|
| `main` | `manage.py` | Sets `DJANGO_SETTINGS_MODULE` and runs management commands. |
| `ImageUpload`, `AnnotationCategory`, `AnnotationRow` | `app/models.py` | ORM models (see §6). |
| `ImageUploadAdmin`, `AnnotationRowAdmin` | `app/admin.py` | List/filter/search in `/admin/`. |
| `AppConfig` | `app/apps.py` | Registers the `app` Django app. |
| URL includes | `core/urls.py`, `app/urls.py` | Routes above. No public `/media/` mount. |
| `generate_test_image`, `_owned_image`, `_response_bytes` | `app/tests.py` | Test fixtures; streaming `FileResponse` helper. |
| `blur_polygon` | `test.py` | Prototype: mask + Gaussian + paste. |
| `blur_image` | `test.py` | Opens a file, blurs multiple regions, saves. |

---

## 10. Frontend function reference

Auth (logged-out page, inline in `home.html`): `setAuthTab(tabName)` toggles signup vs login forms.

Logged-in page sets `window.BLUR_EDITOR = { userId, uploadUrl, hasImages }` and loads `{% static 'app/editor.js' %}`.

### Download folder (File System Access + IndexedDB)

| Function | What it does |
|----------|----------------|
| `supportsDownloadFolderPicker` | Detects `showDirectoryPicker`. |
| `openDownloadFolderDb` | Opens IndexedDB `blur_image_download_folders`. |
| `persistDownloadDirHandle` | Stores directory handle keyed by user id. |
| `loadPersistedDownloadDirHandle` | Restores handle on next visit. |
| `clearPersistedDownloadDirHandle` | Removes saved handle. |
| `ensureDirectoryWritable` | `queryPermission` / `requestPermission` readwrite. |
| `updateDownloadFolderUI` | Hint text + show/hide “Use default folder”. |
| `initDownloadFolderUI` | Disable picker on unsupported browsers; load saved folder. |

### HTTP helpers

| Function | What it does |
|----------|----------------|
| `thumbUrl(url)` | Appends `?thumb=1` (or `&thumb=1`) for gallery images. |
| `getCsrfToken` | CSRF from `<meta>` or `csrftoken` cookie. |
| `parseResponsePayload` | JSON or extract error text from HTML error pages. |
| `requestJson` | `fetch` + parse; `credentials: 'same-origin'`; adds CSRF on non-GET. |

### Editor / zoom / overlay

| Function | What it does |
|----------|----------------|
| `emptyImageState` | Default editor state object. |
| `setEditorEnabled` | Enable/disable Blur and Restore. |
| `refreshUpdatedImageActionsVisibility` | Show Download/Delete Edited if any `data-edited="true"`. |
| `openDeleteUpdatedModal` / `closeDeleteUpdatedModal` | Confirm bulk delete. |
| `resetSelectionPoints` | Clear in-progress and completed shapes. |
| `getCanvasPoint` | Map mouse → image pixels (§5.3). |
| `updateZoomLabel` | Show e.g. `125%`. |
| `getViewFitSize` | Fit natural size into the preview pane (max 860×480, never upscale). |
| `viewBoxUnitsPerScreenPixel` | Image units per CSS pixel (for overlay stroke scale). |
| `overlayPointRadius` / `overlayStrokeWidth` | Zoom-stable handle sizes. |
| `applyZoom` | Set CSS size of canvas + SVG. |
| `resetZoom` | Zoom 1×, scroll to origin. |
| `changeZoom(delta, x, y)` | Clamp 1–4×; keep point under cursor. |
| `finalizeCurrentShape` | If ≥ 2 points, push `{points, category}` and start a new shape. |
| `undoLastPoint` | Ctrl/Cmd+Z: pop last vertex or reopen last shape. |
| `orderPointsClockwise` | Same polar sort as the server. |
| `closedPolygonPoints` | Clockwise order if ≥ 4 points. |
| `drawShapeOnOverlay` | SVG polyline / polygon / dots + category label at centroid. |
| `buildEditorSurface` | Recreate scroll viewport + canvas + SVG. |
| `shapePoints` / `shapeCategory` | Normalize shape object vs bare array. |
| `drawOverlay` | Redraw all completed shapes + current shape. |
| `fitImageSize` | Compute CSS fit box (used by zoom). |
| `renderPreviewCanvas` | `drawImage` at natural resolution into canvas. |
| `registerCanvasPointing` | Wheel zoom; click add point; dblclick finish shape. |
| `updatePreviewDetails` | Title + `W × H pixels`. |
| `revokeEditorObjectUrl` | Free blob URL to avoid leaks. |
| `loadImageIntoEditor` | Fetch **full** image (cache-bust), decode, build surface, reset zoom/shapes. |
| `selectImage` | Highlight thumbnail and load it. |
| `attachGalleryEvents` | Bind preview + delete buttons. |
| `createImageItem` | DOM for a new card after upload (`img` uses `thumbUrl`). |
| `refreshSelectionAfterDelete` | Pick next card or empty state; revokes object URLs. |

### Actions

| Function | What it does |
|----------|----------------|
| `deleteImage` | `POST /delete/<id>/`, remove card, refresh selection. |
| `blurSelectedArea` | Group shapes by category → `POST /edit/<id>/` → download → delete card → next image. |
| `restoreOriginalImage` | `POST /restore/<id>/`, refresh thumb + editor. |
| `sanitizeDownloadFileName` | Strip path and illegal filename chars. |
| `saveDownloadBlob` | Write via directory handle, else trigger browser download. |
| `downloadImageByUrl` | `GET ...?download=1`, then `saveDownloadBlob`. |
| `isImageFile` | MIME or extension filter for folder upload. |
| `uploadSelectedImages` | `FormData` multi-upload; folder paths flattened with `_`. |

Event listeners: file inputs, zoom buttons, blur, restore, delete-edited modal, Escape, Ctrl+Z, window resize.

---

## 11. Tests

`app/tests.py` covers:

- Home page renders for a logged-in user.
- Upload saves working + original + thumbnail; **byte-identical** when no EXIF rotate; resolution reported correctly; `user` is set.
- Delete removes the row.
- Edit replaces the file, keeps JPEG + resolution, sets `is_edited`, refreshes thumbnail.
- Serve + download Content-Disposition uses original filename (`FileResponse` streaming).
- `?thumb=1` returns a JPEG no larger than 216 px.
- Annotations persist / update by category; Excel has one sheet per category and optional single-image filter.
- Restore copies original bytes back and clears `is_edited`.

Run: `python manage.py test app`.

---

## 12. Design choices summary

| Choice | Reason |
|--------|--------|
| Server-side Pillow blur | Full resolution, correct Gaussian neighborhood, format control. |
| Padded-crop blur + mask composite | Clean region edges without blurring unused megapixels. |
| Dual FileFields + thumbnail | Instant restore; cheap gallery. |
| Canvas at natural size + CSS zoom | Pixel-accurate labels; large images still navigable. |
| Categories as TextChoices | Constrained vocabulary for dataset export. |
| Excel via openpyxl | Shareable with annotators / Excel-based pipelines. |
| Direct `user` FK on `ImageUpload` | One owner per file; no unused M2M junction. |
| Auth-gated `serve_image` only | Images are not world-readable via `/media/`. |
| File System Access + IndexedDB | High-volume annotation: dump blurred files into a known folder. |
| Post-blur download + delete-from-gallery | Queue-style processing; skip a wasted preview reload. |

---

## 13. Prototype vs production

`test.py` is the algorithm in isolation: hard-coded polygons, `blur_radius=15`, full-frame blur, `paste` onto RGB. Production uses radius **22**, a **padded crop**, EXIF, format preservation, thumbnails, auth, persistence, multi-shape categories, and a browser editor that sends the same kind of coordinate lists.
