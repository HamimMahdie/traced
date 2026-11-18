"""
Face recognition engine — powered by InsightFace (ArcFace).

Uses the 'buffalo_l' model pack which includes:
  • RetinaFace  — face detection  (high recall, handles small/angled faces well)
  • ArcFace     — face recognition (99.83% accuracy on LFW benchmark)

Responsibilities:
  • Detect faces in an image and return 512-d ArcFace embeddings.
  • Compare an unknown embedding against a set of known reference embeddings.
  • Walk a directory tree, score every .jpg, and yield progress updates.
"""

import os
import cv2
import numpy as np
from PIL import Image
import io
import insightface
from insightface.app import FaceAnalysis

# Supported extensions for the MVP (.jpg only). Easy to extend later.
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg"}

# ── Singleton model loader ───────────────────────────────────────────────────

_face_app: FaceAnalysis | None = None


def _get_models_root() -> str:
    """Determine the root directory where InsightFace models reside."""
    if "INSIGHTFACE_ROOT" in os.environ:
        return os.environ["INSIGHTFACE_ROOT"]

    # Check if ./models/buffalo_l exists in the project directory
    project_dir = os.path.dirname(os.path.abspath(__file__))
    local_models = os.path.join(project_dir, "models", "buffalo_l")
    if os.path.isdir(local_models):
        return project_dir

    return os.path.expanduser("~/.insightface")


def _get_face_app() -> FaceAnalysis:
    """
    Lazily initialize the InsightFace app with the buffalo_l model pack.
    Loads from local ./models or ~/.insightface (downloads on first run if missing).
    """
    global _face_app
    if _face_app is None:
        root_dir = _get_models_root()
        _face_app = FaceAnalysis(
            name="buffalo_l",
            root=root_dir,
            providers=["CPUExecutionProvider"],
        )
        # det_size controls the detection resolution — higher = better detection
        # of smaller faces, but slower.  640x640 is a good balance.
        _face_app.prepare(ctx_id=-1, det_size=(640, 640))
    return _face_app


# ── Core functions ───────────────────────────────────────────────────────────


def detect_and_encode(image_path: str) -> list[np.ndarray]:
    """
    Load an image, detect all faces, and return a list of 512-d ArcFace
    embeddings (one per detected face).

    Returns an empty list if no faces are found.
    """
    app = _get_face_app()
    img = cv2.imread(image_path)
    if img is None:
        return []
    faces = app.get(img)
    return [face.embedding for face in faces]


def detect_and_encode_from_bytes(image_bytes: bytes) -> list[np.ndarray]:
    """
    Same as detect_and_encode but accepts raw bytes (for uploaded files).
    """
    app = _get_face_app()
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return []
    faces = app.get(img)
    return [face.embedding for face in faces]


def compare_face(
    known_encodings: list[np.ndarray],
    unknown_encoding: np.ndarray,
    tolerance: float = 0.4,
) -> tuple[bool, float]:
    """
    Compare a single unknown face embedding against all known reference
    encodings using cosine similarity.

    InsightFace ArcFace embeddings are already normalized, so cosine
    similarity = dot product.

    Args:
        known_encodings: List of reference 512-d embeddings.
        unknown_encoding: A single 512-d embedding to test.
        tolerance: Minimum cosine similarity to count as a match.
                   Range [0, 1]. Higher = stricter. Default 0.4 is
                   a good starting point; 0.5+ is very strict.

    Returns:
        (is_match, best_similarity)
    """
    if not known_encodings:
        return False, 0.0

    # Normalize embeddings (they should already be, but just in case)
    unknown_norm = unknown_encoding / (np.linalg.norm(unknown_encoding) + 1e-10)

    best_sim = -1.0
    for known in known_encodings:
        known_norm = known / (np.linalg.norm(known) + 1e-10)
        sim = float(np.dot(known_norm, unknown_norm))
        if sim > best_sim:
            best_sim = sim

    is_match = best_sim >= tolerance
    return is_match, best_sim


def collect_jpg_files(directory: str) -> list[str]:
    """
    Recursively collect all supported image files under `directory`.
    Ignores previous 'Traced' output folders and hidden directories.
    """
    results = []
    for root, dirs, files in os.walk(directory):
        # Exclude 'Traced' output folders and hidden directories from scanning
        dirs[:] = [d for d in dirs if d != "Traced" and not d.startswith(".")]
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext in SUPPORTED_EXTENSIONS:
                results.append(os.path.join(root, fname))
    return sorted(results)


def scan_directory(
    directory: str,
    known_encodings: list[np.ndarray],
    tolerance: float = 0.4,
):
    """
    Generator that walks `directory`, scores every .jpg, and yields progress
    dicts suitable for streaming to the frontend via SSE.

    Yields dicts with keys:
        type        — "progress" | "match" | "done" | "error"
        current     — current file index (1-based)
        total       — total file count
        file        — absolute path of the current file
        similarity  — best cosine similarity (only for "match")
        message     — human-readable status
    """
    # Force model load before scanning so the first image isn't slow
    _get_face_app()

    files = collect_jpg_files(directory)
    total = len(files)

    if total == 0:
        yield {
            "type": "done",
            "current": 0,
            "total": 0,
            "matches": 0,
            "message": "No .jpg files found in the directory.",
        }
        return

    match_count = 0

    for idx, filepath in enumerate(files, start=1):
        try:
            encodings = detect_and_encode(filepath)

            matched = False
            best_sim = 0.0

            for enc in encodings:
                is_match, sim = compare_face(known_encodings, enc, tolerance)
                if is_match and sim > best_sim:
                    matched = True
                    best_sim = sim

            if matched:
                match_count += 1
                yield {
                    "type": "match",
                    "current": idx,
                    "total": total,
                    "file": filepath,
                    "similarity": round(best_sim, 4),
                    "message": f"Match found: {os.path.basename(filepath)}",
                }

            # Emit progress every file
            yield {
                "type": "progress",
                "current": idx,
                "total": total,
                "matches": match_count,
                "message": f"Scanned {idx}/{total}",
            }

        except Exception as exc:
            yield {
                "type": "error",
                "current": idx,
                "total": total,
                "file": filepath,
                "message": f"Error processing {os.path.basename(filepath)}: {exc}",
            }

    yield {
        "type": "done",
        "current": total,
        "total": total,
        "matches": match_count,
        "message": f"Scan complete. {match_count} match(es) found in {total} images.",
    }


# ── Image utilities ──────────────────────────────────────────────────────────


def make_thumbnail(image_path: str, max_size: int = 300) -> bytes:
    """
    Return JPEG bytes for a thumbnail of the given image.
    """
    img = Image.open(image_path)
    img.thumbnail((max_size, max_size), Image.LANCZOS)
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    buf.seek(0)
    return buf.read()


def crop_face(image_path: str, max_size: int = 200) -> bytes | None:
    """
    Detect the first face in `image_path`, crop it, and return JPEG bytes.
    Returns None if no face is found.
    """
    app = _get_face_app()
    img = cv2.imread(image_path)
    if img is None:
        return None

    faces = app.get(img)
    if not faces:
        return None

    # Use the bounding box from the first detected face
    bbox = faces[0].bbox.astype(int)  # [x1, y1, x2, y2]
    h, w = img.shape[:2]

    # Add padding
    pad_x = int((bbox[2] - bbox[0]) * 0.2)
    pad_y = int((bbox[3] - bbox[1]) * 0.2)
    x1 = max(0, bbox[0] - pad_x)
    y1 = max(0, bbox[1] - pad_y)
    x2 = min(w, bbox[2] + pad_x)
    y2 = min(h, bbox[3] + pad_y)

    face_crop = img[y1:y2, x1:x2]

    # Convert BGR to RGB for PIL
    face_crop_rgb = cv2.cvtColor(face_crop, cv2.COLOR_BGR2RGB)
    face_img = Image.fromarray(face_crop_rgb)
    face_img.thumbnail((max_size, max_size), Image.LANCZOS)

    buf = io.BytesIO()
    face_img.save(buf, format="JPEG", quality=85)
    buf.seek(0)
    return buf.read()


def crop_face_from_bytes(image_bytes: bytes, max_size: int = 200) -> bytes | None:
    """
    Same as crop_face but accepts raw image bytes.
    """
    app = _get_face_app()
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return None

    faces = app.get(img)
    if not faces:
        return None

    bbox = faces[0].bbox.astype(int)
    h, w = img.shape[:2]

    pad_x = int((bbox[2] - bbox[0]) * 0.2)
    pad_y = int((bbox[3] - bbox[1]) * 0.2)
    x1 = max(0, bbox[0] - pad_x)
    y1 = max(0, bbox[1] - pad_y)
    x2 = min(w, bbox[2] + pad_x)
    y2 = min(h, bbox[3] + pad_y)

    face_crop = img[y1:y2, x1:x2]
    face_crop_rgb = cv2.cvtColor(face_crop, cv2.COLOR_BGR2RGB)
    face_img = Image.fromarray(face_crop_rgb)
    face_img.thumbnail((max_size, max_size), Image.LANCZOS)

    buf = io.BytesIO()
    face_img.save(buf, format="JPEG", quality=85)
    buf.seek(0)
    return buf.read()
