"""
Flask backend for Trace — Facial Recognition Photo Filter.

Endpoints:
    GET  /                        — Serve the frontend
    POST /api/upload-reference    — Upload reference face image(s)
    GET  /api/references          — List current reference face crops
    DELETE /api/references        — Clear all references
    POST /api/scan                — Start scanning a directory (SSE stream)
    GET  /api/thumbnail           — Serve a resized thumbnail for a file
    POST /api/copy                — Copy matched files to output directory
"""

import json
import os
import shutil
import base64
import threading
from flask import Flask, request, jsonify, Response, send_from_directory, send_file
from io import BytesIO

import face_engine

app = Flask(__name__, static_folder="static", static_url_path="/static")

# ── In-memory state ──────────────────────────────────────────────────────────

# List of 512-d ArcFace numpy arrays — one per detected reference face
reference_encodings: list = []

# Base64-encoded JPEG crops of the reference faces (for display)
reference_crops: list[str] = []

# Last scan results (list of matched file paths)
last_scan_results: list[str] = []
last_scan_directory: str = ""

# Lock for scan state
scan_lock = threading.Lock()
scan_in_progress = False


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/upload-reference", methods=["POST"])
def upload_reference():
    """
    Accept one or more image files, detect faces, store encodings.
    Returns the number of faces detected.
    """
    if "files" not in request.files:
        return jsonify({"error": "No files provided"}), 400

    files = request.files.getlist("files")
    faces_added = 0

    try:
        for f in files:
            image_bytes = f.read()

            # Detect and encode
            encodings = face_engine.detect_and_encode_from_bytes(image_bytes)
            if not encodings:
                continue

            # Take the first (largest/most prominent) face per image
            reference_encodings.append(encodings[0])
            faces_added += 1

            # Generate a face crop for display
            crop_bytes = face_engine.crop_face_from_bytes(image_bytes)
            if crop_bytes:
                reference_crops.append(base64.b64encode(crop_bytes).decode("ascii"))
            else:
                # Fallback: use a small thumbnail of the whole image
                from PIL import Image
                import io
                img = Image.open(io.BytesIO(image_bytes))
                img.thumbnail((150, 150))
                if img.mode != "RGB":
                    img = img.convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=80)
                reference_crops.append(base64.b64encode(buf.getvalue()).decode("ascii"))

        return jsonify({
            "faces_added": faces_added,
            "total_references": len(reference_encodings),
        })
    except Exception as e:
        app.logger.exception("Failed to process reference image")
        return jsonify({"error": f"Failed to process image: {str(e)}"}), 500


@app.route("/api/references", methods=["GET"])
def get_references():
    """Return the list of reference face crops as base64 strings."""
    return jsonify({
        "count": len(reference_encodings),
        "crops": reference_crops,
    })


@app.route("/api/references", methods=["DELETE"])
def clear_references():
    """Clear all reference encodings."""
    reference_encodings.clear()
    reference_crops.clear()
    return jsonify({"message": "All references cleared."})


@app.route("/api/references/<int:idx>", methods=["DELETE"])
def delete_reference(idx):
    """Delete a specific reference by index."""
    if 0 <= idx < len(reference_encodings):
        reference_encodings.pop(idx)
        reference_crops.pop(idx)
        return jsonify({
            "message": f"Reference {idx} removed.",
            "total_references": len(reference_encodings),
        })
    return jsonify({"error": "Index out of range"}), 404


@app.route("/api/scan", methods=["POST"])
def scan():
    """
    Start scanning a directory. Returns an SSE stream.

    JSON body:
        directory  — absolute path to scan
        tolerance  — float, cosine similarity threshold (default 0.4)
    """
    global scan_in_progress, last_scan_results, last_scan_directory

    if not reference_encodings:
        return jsonify({"error": "No reference faces uploaded. Please add at least one reference first."}), 400

    data = request.get_json(silent=True) or {}
    directory = data.get("directory", "").strip()
    tolerance = float(data.get("tolerance", 0.4))

    if not directory:
        return jsonify({"error": "No directory path provided."}), 400

    if not os.path.isdir(directory):
        return jsonify({"error": f"Directory not found: {directory}"}), 404

    with scan_lock:
        if scan_in_progress:
            return jsonify({"error": "A scan is already in progress."}), 409
        scan_in_progress = True

    last_scan_results = []
    last_scan_directory = directory

    def generate():
        global scan_in_progress
        try:
            for event in face_engine.scan_directory(directory, reference_encodings, tolerance):
                if event["type"] == "match":
                    last_scan_results.append(event["file"])
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            with scan_lock:
                scan_in_progress = False

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/api/thumbnail", methods=["GET"])
def thumbnail():
    """
    Serve a thumbnail for a given file path.
    Query param: path — absolute file path
    """
    filepath = request.args.get("path", "")
    if not filepath or not os.path.isfile(filepath):
        return jsonify({"error": "File not found"}), 404

    try:
        thumb_bytes = face_engine.make_thumbnail(filepath, max_size=300)
        return send_file(BytesIO(thumb_bytes), mimetype="image/jpeg")
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/image", methods=["GET"])
def full_image():
    """
    Serve the full-resolution image for preview.
    Query param: path — absolute file path
    """
    filepath = request.args.get("path", "")
    if not filepath or not os.path.isfile(filepath):
        return jsonify({"error": "File not found"}), 404

    try:
        return send_file(filepath, mimetype="image/jpeg")
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/copy", methods=["POST"])
def copy_files():
    """
    Copy all matched files to an output directory, preserving subdirectory structure.
    If no output directory is provided, defaults to a 'Traced' folder inside the scanned directory.

    JSON body:
        output_directory (optional) — absolute path for the destination
    """
    data = request.get_json(silent=True) or {}
    output_dir = data.get("output_directory", "").strip()

    if not output_dir:
        if last_scan_directory:
            output_dir = os.path.join(last_scan_directory, "Traced")
        else:
            return jsonify({"error": "No output directory provided and no previous scan directory found."}), 400

    if not last_scan_results:
        return jsonify({"error": "No scan results to copy. Run a scan first."}), 400

    copied = 0
    errors = []

    for src in last_scan_results:
        try:
            # Compute the relative path from the scanned directory
            rel_path = os.path.relpath(src, last_scan_directory)
            dest = os.path.join(output_dir, rel_path)

            # Avoid copying a file onto itself if already in destination
            if os.path.abspath(src) == os.path.abspath(dest):
                continue

            # Create parent directories
            os.makedirs(os.path.dirname(dest), exist_ok=True)

            # Copy with metadata
            shutil.copy2(src, dest)
            copied += 1
        except Exception as e:
            errors.append({"file": src, "error": str(e)})

    return jsonify({
        "copied": copied,
        "total": len(last_scan_results),
        "errors": errors,
        "output_directory": output_dir,
        "message": f"Successfully copied {copied}/{len(last_scan_results)} files to '{output_dir}'.",
    })


@app.route("/api/results", methods=["GET"])
def get_results():
    """Return the last scan results (matched file paths)."""
    return jsonify({
        "matches": last_scan_results,
        "count": len(last_scan_results),
        "scan_directory": last_scan_directory,
    })


if __name__ == "__main__":
    print("\n  🔍 Trace — Facial Recognition Photo Filter")
    print("  ────────────────────────────────────────────")
    print("  Running at: http://localhost:5050\n")
    app.run(debug=True, port=5050, threaded=True)
