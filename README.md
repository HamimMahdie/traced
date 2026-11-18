# Trace — Facial Recognition Photo Filter

**Trace** is a local web application that scans directories of photos to find every image of a specific person using deep-learning face recognition. Once scanned, you can preview the matches and automatically copy them into an organized folder.

Powered by **InsightFace** (`buffalo_l` model pack featuring **RetinaFace** for face detection and **ArcFace** for 512-dimensional face recognition embeddings).

---

## Features

- **High-Accuracy Face Recognition**: Uses RetinaFace + ArcFace models (99.8%+ benchmark accuracy).
- **Multi-Angle Reference Faces**: Upload multiple reference photos (front, profile, different lighting) to maximize match recall.
- **Real-Time Directory Scanning**: Live progress updates streamed over Server-Sent Events (SSE).
- **Adjustable Similarity Threshold**: Fine-tune precision vs. recall with an interactive slider.
- **Visual Match Review & Lightbox**: Interactive thumbnail grid with a full-resolution image preview lightbox.
- **Folder-Preserving Batch Copy**: Copies all matched photos to an output folder while preserving original subdirectory hierarchy.
- **100% Local & Private**: All processing happens entirely on your machine.

---

## Prerequisites

- **Python**: Version 3.10 to 3.13
- **Operating System**: macOS, Linux, or Windows
- **Internet Access (First Run Only)**: Automatically downloads the InsightFace `buffalo_l` model pack (~300 MB) on first use.

---

## Installation

### 1. Clone or Open the Repository

```bash
cd "Facial Recognition App"
```

### 2. Create and Activate a Virtual Environment

* **macOS / Linux:**
  ```bash
  python3 -m venv venv
  source venv/bin/activate
  ```

* **Windows (Command Prompt):**
  ```cmd
  python -m venv venv
  venv\Scripts\activate.bat
  ```

* **Windows (PowerShell):**
  ```powershell
  python -m venv venv
  .\venv\Scripts\Activate.ps1
  ```

### 3. Install Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

---

## Running the App

### Start the Server

With your virtual environment activated:

```bash
python app.py
```

Or run directly using the environment binary:

```bash
./venv/bin/python app.py
```

### Open the Application

Navigate to:
```
http://localhost:5050
```

> [!NOTE]
> The server runs by default on port `5050`. To stop the server at any time, press `Ctrl + C` in the terminal.

---

## Usage Guide

### Step 1: Upload Reference Faces
1. Drag and drop or click to upload one or more clear photos of the target person in the **Reference Faces** panel.
2. The app detects the main face in each photo, crops it, and extracts a 512-dimensional embedding.
3. **Tips for best results:**
   - Upload 2–5 photos showing varied angles (front-facing, slight profile, smiling, neutral).
   - Avoid blurry or heavily occluded reference images.
   - Individual reference faces can be removed by hovering over their card and clicking the remove icon, or cleared with **Clear All**.

### Step 2: Set Scan Directory & Similarity Threshold
1. Enter the absolute path of the directory containing the photos you want to search in the **Directory Path** field (e.g., `/Users/username/Pictures/Vacation2024`).
2. Adjust the **Similarity Threshold** slider (cosine similarity between 0.20 and 0.70):
   - **0.40 (Default - Recommended)**: Balanced accuracy and recall for most general photo collections.
   - **0.30 – 0.35 (Permissive)**: Helpful for side-profiles, hats, sunglasses, distant faces, or low-light shots (may increase false positives).
   - **0.45 – 0.55+ (Strict)**: Near-zero false positives, but may miss difficult or distant angles.

### Step 3: Run the Scan
1. Click **Start Scan**.
2. Watch the real-time progress bar, scanned photo count, match counter, and hit rate.
3. You can stop the scan at any point by clicking **Stop**.

### Step 4: Preview Matches
1. Once matches are found, sample thumbnails will appear in the **Results** panel.
2. Click any thumbnail to open the full-resolution preview lightbox.
3. Use the arrow keys or the on-screen navigation buttons (`‹` / `›`) to browse through matches.

### Step 5: Export / Copy Matches
1. In the **Copy matches to directory** field:
   - Leave blank to use the default destination: `<Scanned Directory>/Traced`
   - Or enter a custom absolute directory path (e.g., `/Users/username/Desktop/Filtered_Photos`).
2. Click **Copy All Matches**.
3. Files will be copied preserving the relative directory structure of the scanned source folder.

---

## Project Structure

```
Facial Recognition App/
├── app.py              # Flask server and REST/SSE API endpoints
├── face_engine.py      # Face detection, embedding extraction, and directory scanner
├── requirements.txt    # Python dependencies
├── README.md           # Documentation and usage guide
└── static/
    ├── index.html      # Frontend HTML template
    ├── style.css       # Custom styles and dark-mode UI
    └── app.js          # Client-side UI logic, drag & drop, SSE consumer, lightbox
```

---

## API Reference

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/` | Serves the web interface (`index.html`). |
| `POST` | `/api/upload-reference` | Accepts multipart image uploads; extracts and registers reference embeddings. |
| `GET` | `/api/references` | Returns the list of registered reference face crops (base64) and count. |
| `DELETE` | `/api/references` | Clears all stored reference face embeddings. |
| `DELETE` | `/api/references/<id>` | Removes a specific reference face by its 0-based index. |
| `POST` | `/api/scan` | Initiates directory scanning; streams progress and matches via Server-Sent Events (SSE). |
| `GET` | `/api/thumbnail?path=<filepath>` | Returns an on-the-fly resized JPEG thumbnail (300px max) for quick preview. |
| `GET` | `/api/image?path=<filepath>` | Returns the full-resolution image for preview modal. |
| `POST` | `/api/copy` | Copies all matched files from the latest scan to the specified destination path. |
| `GET` | `/api/results` | Returns the list of matched file paths from the most recent scan. |

---

## Troubleshooting & FAQ

#### 1. Why is the first scan or reference upload taking a few seconds to start?
InsightFace lazily downloads the `buffalo_l` model weights (~300 MB) on its very first run and caches them locally under `~/.insightface/models/`. Subsequent scans load from disk instantly.

#### 2. What image formats are supported?
The scanner currently indexes `.jpg` and `.jpeg` files. Reference uploads accept `.jpg`, `.jpeg`, `.png`, and `.webp`.

#### 3. How do I change the port?
If port `5050` is already in use by another application, open [app.py](file:///Users/hamimmahdie/Developer/Facial%20Recognition%20App/app.py#L269-L270) and modify the port number in `app.run(..., port=5050)`:
```python
app.run(debug=True, port=8080, threaded=True)
```

