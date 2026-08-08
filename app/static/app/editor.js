(() => {
    const config = window.BLUR_EDITOR || {};
    const CURRENT_USER_ID = config.userId;
    const MIN_ZOOM = 1;
    const MAX_ZOOM = 4;
    const ZOOM_STEP = 0.25;
    const DOWNLOAD_FOLDER_DB = 'blur_image_download_folders';
    const DOWNLOAD_FOLDER_STORE = 'handles';

    const imageUpload = document.getElementById('imageUpload');
    const statusText = document.getElementById('statusText');
    const imageList = document.getElementById('imageList');
    const previewTitle = document.getElementById('previewTitle');
    const previewDimensions = document.getElementById('previewDimensions');
    const imageCount = document.getElementById('imageCount');
    const blurBtn = document.getElementById('blurBtn');
    const clearPointsBtn = document.getElementById('clearPointsBtn');
    const categorySelect = document.getElementById('categorySelect');
    const zoomInBtn = document.getElementById('zoomInBtn');
    const zoomOutBtn = document.getElementById('zoomOutBtn');
    const zoomResetBtn = document.getElementById('zoomResetBtn');
    const folderUpload = document.getElementById('folderUpload');
    const downloadFolderHint = document.getElementById('downloadFolderHint');
    const chooseDownloadFolderBtn = document.getElementById('chooseDownloadFolderBtn');
    const clearDownloadFolderBtn = document.getElementById('clearDownloadFolderBtn');
    const updatedImageActions = document.getElementById('updatedImageActions');
    const deleteUpdatedForm = document.getElementById('deleteUpdatedForm');
    const openDeleteUpdatedModalBtn = document.getElementById('openDeleteUpdatedModalBtn');
    const deleteUpdatedModal = document.getElementById('deleteUpdatedModal');
    const cancelDeleteUpdatedModalBtn = document.getElementById('cancelDeleteUpdatedModalBtn');
    const confirmDeleteUpdatedModalBtn = document.getElementById('confirmDeleteUpdatedModalBtn');
    const editorFrame = document.getElementById('editorFrame');

    let emptyPreview = document.getElementById('emptyPreview');
    let previewCanvas = null;
    let overlaySvg = null;
    let editorViewport = null;
    let currentImageButton = null;
    let displaySize = { width: 0, height: 0 };
    let zoomLevel = 1;
    let completedShapes = [];
    let currentShape = [];
    let canvasClickCount = 0;
    let canvasClickTimer = null;
    let downloadDirHandle = null;
    let currentImageState = emptyImageState();

    function emptyImageState() {
        return { id: null, name: '', url: '', image: null, isEdited: false, objectUrl: null };
    }

    function thumbUrl(url) {
        return `${url}${url.includes('?') ? '&' : '?'}thumb=1`;
    }

    function getCsrfToken() {
        const tokenFromMeta = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content');
        if (tokenFromMeta && tokenFromMeta !== 'NOTPROVIDED') {
            return tokenFromMeta;
        }
        const cookie = document.cookie.split('; ').find((row) => row.startsWith('csrftoken='));
        return cookie ? decodeURIComponent(cookie.split('=')[1]) : '';
    }

    async function parseResponsePayload(response) {
        const contentType = response.headers.get('content-type') || '';
        if (contentType.includes('application/json')) {
            return response.json();
        }
        const text = await response.text();
        const htmlDoc = new DOMParser().parseFromString(text, 'text/html');
        const title = htmlDoc.querySelector('title')?.textContent?.trim();
        const bodyText = htmlDoc.body?.textContent?.replace(/\s+/g, ' ').trim();
        return { error: title || bodyText || 'Unexpected non-JSON response from server.' };
    }

    async function requestJson(url, options = {}) {
        const headers = { ...(options.headers || {}) };
        if (options.method && options.method !== 'GET') {
            headers['X-CSRFToken'] = headers['X-CSRFToken'] || getCsrfToken();
        }
        const response = await fetch(url, { credentials: 'same-origin', ...options, headers });
        return { response, data: await parseResponsePayload(response) };
    }

    function supportsDownloadFolderPicker() {
        return typeof window.showDirectoryPicker === 'function';
    }

    function openDownloadFolderDb() {
        return new Promise((resolve, reject) => {
            const req = indexedDB.open(DOWNLOAD_FOLDER_DB, 1);
            req.onerror = () => reject(req.error);
            req.onupgradeneeded = () => {
                if (!req.result.objectStoreNames.contains(DOWNLOAD_FOLDER_STORE)) {
                    req.result.createObjectStore(DOWNLOAD_FOLDER_STORE);
                }
            };
            req.onsuccess = () => resolve(req.result);
        });
    }

    async function persistDownloadDirHandle(handle) {
        if (!CURRENT_USER_ID || !handle) return;
        try {
            const db = await openDownloadFolderDb();
            await new Promise((resolve, reject) => {
                const tx = db.transaction(DOWNLOAD_FOLDER_STORE, 'readwrite');
                tx.objectStore(DOWNLOAD_FOLDER_STORE).put(handle, CURRENT_USER_ID);
                tx.oncomplete = () => resolve();
                tx.onerror = () => reject(tx.error);
            });
            db.close();
        } catch (error) {
            console.warn('Could not persist download folder for this user', error);
        }
    }

    async function loadPersistedDownloadDirHandle() {
        if (!CURRENT_USER_ID) return null;
        try {
            const db = await openDownloadFolderDb();
            const handle = await new Promise((resolve, reject) => {
                const tx = db.transaction(DOWNLOAD_FOLDER_STORE, 'readonly');
                const request = tx.objectStore(DOWNLOAD_FOLDER_STORE).get(CURRENT_USER_ID);
                request.onsuccess = () => resolve(request.result);
                request.onerror = () => reject(request.error);
            });
            db.close();
            return handle && typeof handle.getFileHandle === 'function' ? handle : null;
        } catch (error) {
            console.warn('Could not load saved download folder for this user', error);
            return null;
        }
    }

    async function clearPersistedDownloadDirHandle() {
        if (!CURRENT_USER_ID) return;
        try {
            const db = await openDownloadFolderDb();
            await new Promise((resolve, reject) => {
                const tx = db.transaction(DOWNLOAD_FOLDER_STORE, 'readwrite');
                tx.objectStore(DOWNLOAD_FOLDER_STORE).delete(CURRENT_USER_ID);
                tx.oncomplete = () => resolve();
                tx.onerror = () => reject(tx.error);
            });
            db.close();
        } catch (error) {
            console.warn(error);
        }
    }

    async function ensureDirectoryWritable(handle) {
        if (!handle || typeof handle.queryPermission !== 'function') return false;
        const opts = { mode: 'readwrite' };
        let state = await handle.queryPermission(opts);
        if (state === 'prompt' && typeof handle.requestPermission === 'function') {
            state = await handle.requestPermission(opts);
        }
        return state === 'granted';
    }

    function updateDownloadFolderUI() {
        if (!downloadFolderHint || !clearDownloadFolderBtn) return;
        if (downloadDirHandle) {
            downloadFolderHint.textContent =
                'Blurred images will be saved into your folder for this account: ' +
                downloadDirHandle.name +
                '. (Each signed-in user can choose a different folder on this browser.)';
            clearDownloadFolderBtn.classList.remove('hidden');
            return;
        }
        downloadFolderHint.textContent =
            'Blurred images use your browser’s default download folder unless you pick one above. The folder is remembered per account on this device.';
        clearDownloadFolderBtn.classList.add('hidden');
    }

    function setEditorEnabled(enabled) {
        blurBtn.disabled = !enabled;
        clearPointsBtn.disabled = !enabled;
    }

    function refreshUpdatedImageActionsVisibility() {
        updatedImageActions.classList.toggle(
            'hidden',
            !document.querySelector('.image-item[data-edited="true"]'),
        );
    }

    function openDeleteUpdatedModal() {
        deleteUpdatedModal.classList.remove('hidden');
        deleteUpdatedModal.classList.add('flex');
    }

    function closeDeleteUpdatedModal() {
        deleteUpdatedModal.classList.add('hidden');
        deleteUpdatedModal.classList.remove('flex');
    }

    function resetSelectionPoints() {
        completedShapes = [];
        currentShape = [];
        canvasClickCount = 0;
        if (canvasClickTimer) {
            clearTimeout(canvasClickTimer);
            canvasClickTimer = null;
        }
        drawOverlay();
    }

    function getCanvasPoint(event) {
        const rect = previewCanvas.getBoundingClientRect();
        return {
            x: Number(((event.clientX - rect.left) * (displaySize.width / rect.width)).toFixed(2)),
            y: Number(((event.clientY - rect.top) * (displaySize.height / rect.height)).toFixed(2)),
        };
    }

    function updateZoomLabel() {
        if (zoomResetBtn) zoomResetBtn.textContent = `${Math.round(zoomLevel * 100)}%`;
    }

    function fitImageSize(width, height) {
        const maxWidth = Math.min(editorFrame.parentElement.clientWidth - 24, 860);
        const maxHeight = window.innerWidth >= 640 ? 480 : 340;
        const scale = Math.min(maxWidth / width, maxHeight / height, 1);
        return {
            width: Math.max(1, Math.round(width * scale)),
            height: Math.max(1, Math.round(height * scale)),
        };
    }

    function getViewFitSize() {
        if (!currentImageState.image) return { width: 0, height: 0 };
        return fitImageSize(currentImageState.image.naturalWidth, currentImageState.image.naturalHeight);
    }

    function viewBoxUnitsPerScreenPixel() {
        const fit = getViewFitSize();
        return currentImageState.image.naturalWidth / Math.max(1, fit.width * zoomLevel);
    }

    function overlayPointRadius() {
        return 5 * viewBoxUnitsPerScreenPixel();
    }

    function overlayStrokeWidth() {
        return 2 * viewBoxUnitsPerScreenPixel();
    }

    function applyZoom() {
        if (!previewCanvas || !overlaySvg) return;
        const fit = getViewFitSize();
        const width = fit.width * zoomLevel;
        const height = fit.height * zoomLevel;
        previewCanvas.style.width = `${width}px`;
        previewCanvas.style.height = `${height}px`;
        overlaySvg.style.width = `${width}px`;
        overlaySvg.style.height = `${height}px`;
        updateZoomLabel();
        drawOverlay();
    }

    function resetZoom() {
        zoomLevel = 1;
        applyZoom();
        if (editorViewport) {
            editorViewport.scrollLeft = 0;
            editorViewport.scrollTop = 0;
        }
    }

    function changeZoom(delta, clientX, clientY) {
        const previousZoom = zoomLevel;
        zoomLevel = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Number((zoomLevel + delta).toFixed(2))));
        if (zoomLevel === previousZoom) return;

        if (editorViewport && clientX != null && clientY != null) {
            const viewportRect = editorViewport.getBoundingClientRect();
            const offsetX = clientX - viewportRect.left + editorViewport.scrollLeft;
            const offsetY = clientY - viewportRect.top + editorViewport.scrollTop;
            const fit = getViewFitSize();
            const ratioX = offsetX / (fit.width * previousZoom);
            const ratioY = offsetY / (fit.height * previousZoom);
            applyZoom();
            editorViewport.scrollLeft = ratioX * fit.width * zoomLevel - (clientX - viewportRect.left);
            editorViewport.scrollTop = ratioY * fit.height * zoomLevel - (clientY - viewportRect.top);
            return;
        }
        applyZoom();
    }

    function finalizeCurrentShape() {
        if (currentShape.length >= 2) {
            completedShapes.push({ points: [...currentShape], category: categorySelect?.value || '' });
        }
        currentShape = [];
        drawOverlay();
    }

    function undoLastPoint() {
        if (currentShape.length > 0) {
            currentShape.pop();
            drawOverlay();
            return true;
        }
        if (completedShapes.length > 0) {
            const popped = completedShapes.pop();
            currentShape = Array.isArray(popped) ? popped : (popped && popped.points) || [];
            currentShape.pop();
            drawOverlay();
            return true;
        }
        return false;
    }

    function orderPointsClockwise(points) {
        if (points.length < 3) return points;
        const centerX = points.reduce((sum, point) => sum + point.x, 0) / points.length;
        const centerY = points.reduce((sum, point) => sum + point.y, 0) / points.length;
        return [...points].sort((left, right) => (
            Math.atan2(left.y - centerY, left.x - centerX) - Math.atan2(right.y - centerY, right.x - centerX)
        ));
    }

    function closedPolygonPoints(points) {
        return points.length >= 4 ? orderPointsClockwise(points) : points;
    }

    function drawShapeOnOverlay(points, category) {
        const strokeWidth = overlayStrokeWidth();
        const pointRadius = overlayPointRadius();

        if (points.length === 2) {
            const polyline = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
            polyline.setAttribute('points', points.map((point) => `${point.x},${point.y}`).join(' '));
            polyline.setAttribute('fill', 'none');
            polyline.setAttribute('stroke', '#fb7185');
            polyline.setAttribute('stroke-width', String(strokeWidth));
            overlaySvg.appendChild(polyline);
        }

        if (points.length >= 3) {
            const polygonPoints = closedPolygonPoints(points);
            const polygon = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
            polygon.setAttribute('points', polygonPoints.map((point) => `${point.x},${point.y}`).join(' '));
            polygon.setAttribute('fill', 'rgba(251, 113, 133, 0.18)');
            polygon.setAttribute('stroke', '#fb7185');
            polygon.setAttribute('stroke-width', String(strokeWidth));
            overlaySvg.appendChild(polygon);
        }

        points.forEach((point) => {
            const dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
            dot.setAttribute('cx', point.x);
            dot.setAttribute('cy', point.y);
            dot.setAttribute('r', String(pointRadius));
            dot.setAttribute('fill', '#ffffff');
            dot.setAttribute('stroke', '#fb7185');
            dot.setAttribute('stroke-width', String(strokeWidth));
            overlaySvg.appendChild(dot);
        });

        if (category && points.length) {
            const cx = points.reduce((sum, point) => sum + point.x, 0) / points.length;
            const cy = points.reduce((sum, point) => sum + point.y, 0) / points.length;
            const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
            const pretty = String(category).replace(/_/g, ' ');
            label.setAttribute('x', String(cx));
            label.setAttribute('y', String(Math.max(12, cy - 8)));
            label.setAttribute('fill', '#fff');
            label.setAttribute('font-size', String(Math.max(11, Math.round(overlayStrokeWidth() * 3))));
            label.setAttribute('font-weight', '600');
            label.setAttribute('text-anchor', 'middle');
            label.setAttribute('stroke', '#000');
            label.setAttribute('stroke-width', '0.6');
            label.setAttribute('paint-order', 'stroke');
            label.textContent = pretty.charAt(0).toUpperCase() + pretty.slice(1);
            overlaySvg.appendChild(label);
        }
    }

    function buildEditorSurface() {
        editorFrame.innerHTML = '';
        editorViewport = document.createElement('div');
        editorViewport.className = 'max-h-[480px] max-w-full overflow-auto rounded-2xl';
        const editorSurface = document.createElement('div');
        editorSurface.className = 'relative inline-block';
        previewCanvas = document.createElement('canvas');
        previewCanvas.id = 'previewCanvas';
        previewCanvas.className = 'block cursor-crosshair rounded-2xl';
        overlaySvg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        overlaySvg.setAttribute('class', 'pointer-events-none absolute inset-0');
        editorSurface.appendChild(previewCanvas);
        editorSurface.appendChild(overlaySvg);
        editorViewport.appendChild(editorSurface);
        editorFrame.appendChild(editorViewport);
    }

    function shapePoints(shape) {
        return Array.isArray(shape) ? shape : (shape && shape.points) || [];
    }

    function shapeCategory(shape) {
        return shape && shape.category ? shape.category : '';
    }

    function drawOverlay() {
        if (!overlaySvg) return;
        overlaySvg.setAttribute('viewBox', `0 0 ${displaySize.width} ${displaySize.height}`);
        overlaySvg.innerHTML = '';
        completedShapes.forEach((shape) => drawShapeOnOverlay(shapePoints(shape), shapeCategory(shape)));
        drawShapeOnOverlay(currentShape, categorySelect?.value || '');
    }

    function renderPreviewCanvas() {
        if (!previewCanvas || !currentImageState.image) return;
        const ctx = previewCanvas.getContext('2d');
        previewCanvas.width = displaySize.width;
        previewCanvas.height = displaySize.height;
        applyZoom();
        ctx.clearRect(0, 0, displaySize.width, displaySize.height);
        ctx.drawImage(currentImageState.image, 0, 0, displaySize.width, displaySize.height);
    }

    function registerCanvasPointing() {
        editorViewport.onwheel = (event) => {
            if (!currentImageState.image) return;
            event.preventDefault();
            changeZoom(event.deltaY < 0 ? ZOOM_STEP : -ZOOM_STEP, event.clientX, event.clientY);
        };

        previewCanvas.onclick = (event) => {
            if (!currentImageState.image) return;
            if (event.altKey) {
                changeZoom(ZOOM_STEP, event.clientX, event.clientY);
                return;
            }
            const point = getCanvasPoint(event);
            canvasClickCount += 1;
            if (canvasClickTimer) clearTimeout(canvasClickTimer);
            canvasClickTimer = window.setTimeout(() => {
                canvasClickTimer = null;
                if (canvasClickCount === 1) {
                    currentShape.push(point);
                    drawOverlay();
                }
                canvasClickCount = 0;
            }, 280);
        };

        previewCanvas.ondblclick = (event) => {
            event.preventDefault();
            if (!currentImageState.image) return;
            canvasClickCount = 0;
            if (canvasClickTimer) {
                clearTimeout(canvasClickTimer);
                canvasClickTimer = null;
            }
            finalizeCurrentShape();
        };
    }

    function updatePreviewDetails(name, width, height) {
        previewTitle.textContent = name || 'No image selected';
        if (!previewDimensions) return;
        previewDimensions.textContent = width && height
            ? `${width} × ${height} pixels · full resolution preserved`
            : '';
    }

    function revokeEditorObjectUrl() {
        if (currentImageState.objectUrl) {
            URL.revokeObjectURL(currentImageState.objectUrl);
            currentImageState.objectUrl = null;
        }
    }

    async function loadImageIntoEditor(url, name, imageId) {
        revokeEditorObjectUrl();
        try {
            const fetchUrl = url.startsWith('http') ? url : new URL(url, window.location.origin).href;
            const response = await fetch(`${fetchUrl}${fetchUrl.includes('?') ? '&' : '?'}v=${Date.now()}`, {
                credentials: 'same-origin',
                cache: 'no-store',
            });
            if (!response.ok) throw new Error('Could not load image.');

            const objectUrl = URL.createObjectURL(await response.blob());
            const image = new Image();
            await new Promise((resolve, reject) => {
                image.onload = () => resolve();
                image.onerror = () => reject(new Error('Could not decode image.'));
                image.src = objectUrl;
            });

            currentImageState = {
                id: imageId,
                name,
                url,
                image,
                isEdited: currentImageState.isEdited,
                objectUrl,
            };
            editorFrame.classList.remove('hidden');
            if (emptyPreview) emptyPreview.classList.add('hidden');
            buildEditorSurface();
            displaySize = { width: image.naturalWidth, height: image.naturalHeight };
            resetZoom();
            registerCanvasPointing();
            resetSelectionPoints();
            renderPreviewCanvas();
            setEditorEnabled(true);
            updatePreviewDetails(name, image.naturalWidth, image.naturalHeight);
        } catch (error) {
            statusText.textContent = error.message;
        }
    }

    function selectImage(button) {
        document.querySelectorAll('.image-item').forEach((item) => {
            item.classList.remove('ring-2', 'ring-rose-300', 'border-rose-300/50');
        });
        button.classList.add('ring-2', 'ring-rose-300', 'border-rose-300/50');
        currentImageButton = button;
        currentImageState.isEdited = button.dataset.edited === 'true';
        loadImageIntoEditor(button.dataset.url, button.dataset.name, button.dataset.id);
        if (emptyPreview) {
            emptyPreview.remove();
            emptyPreview = null;
        }
    }

    function attachGalleryEvents() {
        document.querySelectorAll('.image-item').forEach((button) => {
            button.onclick = () => selectImage(button);
        });
        document.querySelectorAll('.delete-image').forEach((button) => {
            button.onclick = async (event) => {
                event.stopPropagation();
                try {
                    await deleteImage(button.dataset.id);
                } catch {
                    /* status set in deleteImage */
                }
            };
        });
    }

    function createImageItem(image) {
        const card = document.createElement('article');
        card.className = 'image-card group relative shrink-0';
        card.dataset.id = image.id;
        card.dataset.url = image.url;
        card.dataset.name = image.name;
        card.dataset.edited = image.is_edited ? 'true' : 'false';
        card.innerHTML = `
            <button
                type="button"
                class="image-item overflow-hidden rounded-[1.5rem] border border-white/10 bg-zinc-900/80 transition hover:border-white/30 hover:bg-zinc-900"
                data-id="${image.id}"
                data-url="${image.url}"
                data-name="${image.name}"
                data-edited="${image.is_edited ? 'true' : 'false'}"
                aria-label="Preview ${image.name}"
            >
                <img src="${thumbUrl(image.url)}" alt="${image.name}" class="h-[96px] w-[96px] object-cover lg:h-[108px] lg:w-[108px]">
            </button>
            <button
                type="button"
                class="delete-image absolute -right-2 -top-2 flex h-8 w-8 items-center justify-center rounded-full border border-white/15 bg-black/80 text-lg leading-none text-white shadow-lg transition hover:border-rose-300 hover:text-rose-300"
                data-id="${image.id}"
                aria-label="Delete ${image.name}"
            >
                x
            </button>
        `;
        return card;
    }

    function refreshSelectionAfterDelete(preferredIndex) {
        const remainingItems = document.querySelectorAll('.image-item');
        imageCount.textContent = String(remainingItems.length);

        if (!remainingItems.length) {
            revokeEditorObjectUrl();
            previewTitle.textContent = 'No image selected';
            currentImageButton = null;
            currentImageState = emptyImageState();
            editorFrame.classList.add('hidden');
            editorFrame.innerHTML = '';
            setEditorEnabled(false);
            resetSelectionPoints();
            if (!emptyPreview) {
                emptyPreview = document.createElement('p');
                emptyPreview.id = 'emptyPreview';
                emptyPreview.className = 'text-xl text-zinc-400';
                emptyPreview.textContent = 'No image selected';
                editorFrame.parentElement.appendChild(emptyPreview);
            }
            return;
        }

        if (preferredIndex !== undefined && preferredIndex !== null) {
            const nextCards = imageList.querySelectorAll('.image-card');
            const pick = Math.min(Math.max(0, preferredIndex), nextCards.length - 1);
            const nextBtn = nextCards[pick]?.querySelector('.image-item');
            if (nextBtn) selectImage(nextBtn);
            return;
        }

        if (!document.querySelector('.image-item.ring-2')) {
            selectImage(remainingItems[0]);
        }
    }

    async function deleteImage(imageId, options = {}) {
        const { preferredIndex, finalStatus, skipLoading, rethrow } = options;
        if (!skipLoading) statusText.textContent = 'Deleting image...';

        try {
            const { response, data } = await requestJson(`/delete/${imageId}/`, { method: 'POST' });
            if (!response.ok) throw new Error(data.error || 'Delete failed.');

            document.querySelector(`.image-card[data-id="${data.deleted_id}"]`)?.remove();
            refreshSelectionAfterDelete(preferredIndex);
            refreshUpdatedImageActionsVisibility();
            statusText.textContent = finalStatus ?? 'Image deleted successfully.';
        } catch (error) {
            statusText.textContent = error.message;
            if (rethrow) throw error;
        }
    }

    async function blurSelectedArea() {
        const shapesToBlur = [
            ...completedShapes.map((shape) => ({ points: shapePoints(shape), category: shapeCategory(shape) })),
            ...(currentShape.length >= 2 ? [{ points: currentShape, category: categorySelect?.value || '' }] : []),
        ];

        if (!currentImageState.image || !shapesToBlur.length) {
            statusText.textContent = 'Click points to draw a shape. Double-click to finish it and start another.';
            return;
        }

        statusText.textContent = 'Blurring selected area...';
        const groups = {};
        shapesToBlur.forEach((shape) => {
            if (!shape.category) return;
            const points = closedPolygonPoints(shape.points).map((point) => ({ x: point.x, y: point.y }));
            if (points.length >= 2) {
                (groups[shape.category] ||= []).push(points);
            }
        });

        if (!Object.keys(groups).length) {
            statusText.textContent = 'Select a category for each shape before saving.';
            return;
        }

        try {
            const { response, data } = await requestJson(`/edit/${currentImageState.id}/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    annotations: Object.entries(groups).map(([category, shapes]) => ({ category, shapes })),
                }),
            });
            if (!response.ok) throw new Error(data.error || 'Blur failed.');

            const card = currentImageButton.closest('.image-card');
            const cardIndex = card
                ? Array.from(imageList.querySelectorAll('.image-card')).indexOf(card)
                : -1;
            const blurId = currentImageState.id;

            await downloadImageByUrl(data.url, data.name);
            statusText.textContent = 'Removing image and loading next…';
            await deleteImage(blurId, {
                preferredIndex: cardIndex >= 0 ? cardIndex : undefined,
                skipLoading: true,
                finalStatus: 'Blurred image downloaded. Showing next image.',
                rethrow: true,
            });
        } catch (error) {
            statusText.textContent = error.message;
        }
    }

    async function restoreOriginalImage() {
        if (!currentImageState.id) return;
        statusText.textContent = 'Restoring original image...';
        try {
            const { response, data } = await requestJson(`/restore/${currentImageState.id}/`, { method: 'POST' });
            if (!response.ok) throw new Error(data.error || 'Restore failed.');

            resetSelectionPoints();
            currentImageButton.dataset.url = data.url;
            currentImageButton.dataset.edited = data.is_edited ? 'true' : 'false';
            currentImageButton.querySelector('img').src = `${thumbUrl(data.url)}&v=${Date.now()}`;
            const card = currentImageButton.closest('.image-card');
            card.dataset.url = data.url;
            card.dataset.edited = data.is_edited ? 'true' : 'false';
            currentImageState.isEdited = data.is_edited;
            loadImageIntoEditor(data.url, currentImageState.name, currentImageState.id);
            refreshUpdatedImageActionsVisibility();
            statusText.textContent = 'Original image restored.';
        } catch (error) {
            statusText.textContent = error.message;
        }
    }

    function sanitizeDownloadFileName(name) {
        const base = (name || 'blurred-image').split(/[/\\]/).pop() || 'blurred-image';
        return base.replace(/[<>:"|?*]/g, '_').trim() || 'blurred-image';
    }

    async function saveDownloadBlob(blob, filename) {
        const safeName = sanitizeDownloadFileName(filename);
        if (downloadDirHandle && typeof downloadDirHandle.getFileHandle === 'function') {
            const canWrite = await ensureDirectoryWritable(downloadDirHandle);
            if (canWrite) {
                const fileHandle = await downloadDirHandle.getFileHandle(safeName, { create: true });
                const writable = await fileHandle.createWritable();
                await writable.write(blob);
                await writable.close();
                return;
            }
        }
        const objectUrl = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = objectUrl;
        link.download = safeName;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(objectUrl);
    }

    async function downloadImageByUrl(url, filename) {
        if (!url) throw new Error('No image URL to download.');
        const safeName = sanitizeDownloadFileName(filename);
        const baseUrl = url.startsWith('http') ? url : new URL(url, window.location.origin).href;
        const downloadUrl = `${baseUrl}${baseUrl.includes('?') ? '&' : '?'}download=1&v=${Date.now()}`;
        const response = await fetch(downloadUrl, { credentials: 'same-origin', cache: 'no-store' });
        if (!response.ok) throw new Error('Could not download image. The server could not find the file.');
        const blob = await response.blob();
        if (!blob.size) throw new Error('Could not download image. The server returned an empty file.');
        await saveDownloadBlob(blob, safeName);
    }

    function isImageFile(file) {
        if (file.type && file.type.startsWith('image/')) return true;
        return /\.(png|jpe?g|gif|webp|bmp|svg|avif|heic|heif)$/i.test(file.name || '');
    }

    async function uploadSelectedImages(files) {
        const formData = new FormData();
        Array.from(files).forEach((file) => {
            const rel = file.webkitRelativePath;
            let useName = rel && rel !== file.name ? rel.replace(/\\/g, '/') : file.name;
            useName = useName.replace(/\//g, '_');
            formData.append(
                'images',
                useName !== file.name
                    ? new File([file], useName, { type: file.type || 'application/octet-stream' })
                    : file,
            );
        });

        statusText.textContent = 'Uploading images...';
        try {
            const { response, data } = await requestJson(config.uploadUrl, {
                method: 'POST',
                body: formData,
            });
            if (!response.ok) throw new Error(data.error || 'Upload failed.');

            data.images.forEach((image) => imageList.appendChild(createImageItem(image)));
            attachGalleryEvents();

            const items = imageList.querySelectorAll('.image-item');
            if (items.length) selectImage(items[items.length - 1]);

            imageCount.textContent = String(items.length);
            const savedDetails = data.images
                .map((image) => (image.width && image.height ? `${image.name} (${image.width}×${image.height})` : image.name))
                .join(', ');
            statusText.textContent = `${data.images.length} image(s) saved at full resolution: ${savedDetails}`;
            imageUpload.value = '';
            if (folderUpload) folderUpload.value = '';
        } catch (error) {
            statusText.textContent = error.message;
        }
    }

    async function initDownloadFolderUI() {
        if (!supportsDownloadFolderPicker()) {
            chooseDownloadFolderBtn.disabled = true;
            chooseDownloadFolderBtn.title = 'Requires Chrome, Edge, or Opera.';
            downloadFolderHint.textContent =
                'Blurred images use your browser’s default download folder. Choosing a fixed folder requires Chrome, Edge, or Opera.';
            return;
        }
        downloadDirHandle = await loadPersistedDownloadDirHandle();
        updateDownloadFolderUI();
    }

    imageUpload.addEventListener('change', (event) => {
        if (event.target.files.length) uploadSelectedImages(event.target.files);
    });

    folderUpload.addEventListener('change', (event) => {
        const imageFiles = Array.from(event.target.files).filter(isImageFile);
        if (!imageFiles.length) {
            statusText.textContent = 'No image files found in that folder.';
            folderUpload.value = '';
            return;
        }
        uploadSelectedImages(imageFiles);
    });

    chooseDownloadFolderBtn.addEventListener('click', async () => {
        if (!supportsDownloadFolderPicker()) return;
        try {
            downloadDirHandle = await window.showDirectoryPicker({ mode: 'readwrite' });
            await persistDownloadDirHandle(downloadDirHandle);
            updateDownloadFolderUI();
            statusText.textContent =
                `Download folder for this account set to "${downloadDirHandle.name}" (saved on this browser).`;
        } catch (error) {
            if (error.name !== 'AbortError') {
                statusText.textContent = error.message || 'Could not open folder.';
            }
        }
    });

    clearDownloadFolderBtn.addEventListener('click', async () => {
        downloadDirHandle = null;
        await clearPersistedDownloadDirHandle();
        updateDownloadFolderUI();
        statusText.textContent = 'Using default download folder again for this account.';
    });

    clearPointsBtn.addEventListener('click', restoreOriginalImage);
    zoomInBtn.addEventListener('click', () => {
        if (!editorViewport) return;
        const rect = editorViewport.getBoundingClientRect();
        changeZoom(ZOOM_STEP, rect.left + rect.width / 2, rect.top + rect.height / 2);
    });
    zoomOutBtn.addEventListener('click', () => {
        if (!editorViewport) return;
        const rect = editorViewport.getBoundingClientRect();
        changeZoom(-ZOOM_STEP, rect.left + rect.width / 2, rect.top + rect.height / 2);
    });
    zoomResetBtn.addEventListener('click', resetZoom);
    blurBtn.addEventListener('click', blurSelectedArea);
    openDeleteUpdatedModalBtn.addEventListener('click', openDeleteUpdatedModal);
    cancelDeleteUpdatedModalBtn.addEventListener('click', closeDeleteUpdatedModal);
    confirmDeleteUpdatedModalBtn.addEventListener('click', () => deleteUpdatedForm.submit());
    deleteUpdatedModal.addEventListener('click', (event) => {
        if (event.target === deleteUpdatedModal) closeDeleteUpdatedModal();
    });
    document.addEventListener('keydown', (event) => {
        if (event.target.matches('input, textarea, select')) return;
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z' && !event.shiftKey) {
            if (undoLastPoint()) event.preventDefault();
            return;
        }
        if (event.key === 'Escape' && !deleteUpdatedModal.classList.contains('hidden')) {
            closeDeleteUpdatedModal();
        }
    });
    window.addEventListener('resize', () => {
        if (currentImageState.image && previewCanvas) applyZoom();
    });

    (async () => {
        setEditorEnabled(Boolean(config.hasImages));
        await initDownloadFolderUI();
        attachGalleryEvents();
        refreshUpdatedImageActionsVisibility();
        const initialSelectedImage =
            document.querySelector('.image-item.ring-2') || document.querySelector('.image-item');
        if (initialSelectedImage) selectImage(initialSelectedImage);
    })();
})();
