# Dr. B. R. Ambedkar Digital Heritage Archive — SIH 2026 PS 26096 prototype

An AI-powered, hardware-integrated archive kiosk for institutions such as the Dr. Ambedkar International Centre. Visitors search, read, listen to and compile Dr. Ambedkar's writings, speeches and Constituent Assembly debates in English, Hindi and Marathi. Curators digitize manuscripts with an ESP32-CAM station, and the physical display case is monitored by an ESP8266 security node.

## What each part does

| Requirement in the problem statement | Where it lives |
|---|---|
| AI semantic search and knowledge mapping | `backend/rag.py` (multilingual vector + BM25 hybrid search), Knowledge Map screen |
| Full text and summarized access | Library and Reader screens, `/api/summarize` |
| OCR digitization of old documents | ESP32-CAM station + `digitize()` in `backend/app.py` (OpenCV cleanup, Tesseract OCR, AI metadata tagging) |
| Multilingual translation and audio narration | `/api/translate`, `/api/tts`, voice input via `/api/stt` (Whisper) |
| Audio-video archive | Audio & Video screen, files in `backend/data/media` |
| Interactive timeline and storytelling | Timeline screen, smart display wall mode (`/?mode=wall`) |
| AI Research Assistant | Ask the Archive screen: answers only from archive passages, with numbered citations and the original page |
| Secure preservation, metadata, archival management | Curator console: Dublin Core catalog, SHA-256 checksums, untouched preservation masters, audit log, PIN access |
| Hardware: kiosks, smart displays, central server | iPad + touch laptop kiosks, wall display, ESP32-CAM, ESP8266 case node, Arduino accessibility buttons, webcam visitor detection |
| "Compile" writings | My Compilation: visitor collects passages and scans a QR code to take them home |

## Folder structure

```
ambedkar-archive/
  backend/
    app.py            FastAPI server (all API endpoints, SSE live events)
    rag.py            indexing, hybrid search, LLM, translation
    ingest.py         load PDFs / text files into the archive
    motion.py         webcam visitor detection (no images stored)
    serial_bridge.py  Arduino buttons -> kiosk
    requirements.txt
    .env.example
    data/
      timeline.json   curated timeline (verify before public use)
      graph.json      curated knowledge map (verify before public use)
      media/          put documentary clips / photos here
  web/
    kiosk.html        visitor kiosk (iPad, touch laptop, wall display)
    admin.html        curator console
  firmware/
    esp32cam_capture/ ESP32-CAM digitization camera
    esp8266_tamper/   display-case security node
    arduino_buttons/  accessibility buttons
```

## 1. Install (do this tonight — the first run downloads about 1 GB of models)

**Python 3.10 or 3.11** is recommended.

```bash
cd backend
python -m venv .venv
# Windows:  .venv\Scripts\activate      macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
```

**Tesseract OCR** (with Hindi and Marathi):
- Windows: install the UB Mannheim build of Tesseract and tick Hindi and Marathi under "Additional language data" during setup. If it isn't on PATH, set `TESSERACT_CMD` in `.env`.
- Ubuntu: `sudo apt install tesseract-ocr tesseract-ocr-hin tesseract-ocr-mar`
- macOS: `brew install tesseract tesseract-lang`

**Configure:** copy `.env.example` to `.env` and set:
- `LLM_PROVIDER` and `LLM_API_KEY`. A Gemini key from Google AI Studio is the quickest free option. Model names change over time, so if the default fails, set `LLM_MODEL` to a current model listed in AI Studio. Without a key the assistant still works in offline mode and shows the best matching passages.
- `ADMIN_PIN` and `SENSOR_TOKEN`.

## 2. Load the archive (server must be stopped while ingesting)

Download from the dataset sources named in the problem statement: the Dr. Ambedkar Foundation's *Writings and Speeches* volumes (PDF) and the Constituent Assembly Debates. Keep the scope small for the demo: a few key works plus the speeches of 4 November 1948 and 25 November 1949. Embedding runs on CPU, so ingest the page ranges you need rather than whole 600-page volumes.

```bash
python ingest.py "sources/Vol1.pdf" --title "Annihilation of Caste" --pages 25-96 --date 1936 --type Book --source "Dr. Ambedkar Foundation, Writings and Speeches Vol. 1"
python ingest.py "sources/cad_1949-11-25.txt" --title "Closing speech in the Constituent Assembly" --date 1949-11-25 --type Speech --source "Constituent Assembly Debates"
```

Page numbers are the real PDF page numbers, so citations point to the right page. Scanned PDFs without a text layer are OCR'd automatically (`--ocr-lang eng+hin`).

To test the pipeline quickly, ingest one short text file first, then the larger PDFs.

## 3. Run

Put every device on the **same Wi-Fi** (a phone hotspot works best). On Windows, allow Python through the firewall on private networks when prompted, or other devices can't connect.

```bash
python app.py            # prints the kiosk, wall and admin URLs with the laptop IP
python motion.py         # optional: webcam visitor detection (second terminal)
python serial_bridge.py  # optional: Arduino buttons (third terminal)
```

## 4. Kiosk devices

**Touch laptop (main kiosk, voice works here):** open Chrome in kiosk mode.
```
chrome --kiosk --autoplay-policy=no-user-gesture-required http://localhost:8000/
```
On Windows use the full path to `chrome.exe`. Press Alt+F4 to exit. The autoplay flag lets the welcome greeting play when the webcam detects a visitor.

**iPad (visitor kiosk):** open `http://<laptop-ip>:8000/` in Safari, tap Share, then *Add to Home Screen*, and launch it from there for a full-screen app. Lock it to the app with Settings > Accessibility > Guided Access, then triple-click the side or home button in the app to start.

**Voice on the iPad** needs HTTPS, because Safari only allows the microphone on secure pages. Two options:
1. Do voice questions on the touch laptop and use the iPad for touch browsing, listening and QR codes. This is simplest.
2. Run a free HTTPS tunnel: install `cloudflared`, run `cloudflared tunnel --url http://localhost:8000`, open the `https://….trycloudflare.com` address on the iPad, and set `PUBLIC_BASE_URL` in `.env` to that address so QR codes use it too. This needs internet.

**Smart display wall:** any second screen or TV at `http://<laptop-ip>:8000/?mode=wall` cycles through the timeline.

**Curator console:** `http://<laptop-ip>:8000/admin`.

## 5. Hardware

Install boards in Arduino IDE via *Preferences > Additional boards manager URLs*:
- ESP32: `https://espressif.github.io/arduino-esp32/package_esp32_index.json`
- ESP8266: `http://arduino.esp8266.com/stable/package_esp8266com_index.json`

Set your hotspot name and password in each sketch.

### ESP32-CAM (digitization station)
Board: **AI Thinker ESP32-CAM**. Flash `firmware/esp32cam_capture`. The serial monitor (115200) prints its address; enter it in the curator console and click *Save address*.

Programming it with an Arduino Uno if you have no ESP32-CAM-MB adapter (do this **before** flashing the Uno with the buttons sketch):
1. Uno RESET to Uno GND (turns the Uno into a USB-serial bridge).
2. Uno pin 0 (RX) to ESP32-CAM U0R, Uno pin 1 (TX) to ESP32-CAM U0T.
3. Uno 5V to ESP32-CAM 5V, GND to GND.
4. ESP32-CAM GPIO0 to GND (flash mode). Upload, pressing the ESP32-CAM reset button when "Connecting…" appears.
5. Remove the GPIO0 wire and press reset to run.

The Uno sends 5 V signals into a 3.3 V pin here. This usually works, but prefer a 3.3 V USB-serial adapter if you have one. Brownout resets mean weak power, so use a good USB cable and port.

Mount the camera 25–30 cm above the page, facing straight down, so the page fills the frame. Printed pages OCR well; handwritten manuscripts need the production scanner.

### ESP8266 (display-case security node)
Flash `firmware/esp8266_tamper` (board: NodeMCU 1.0). Set `BACKEND` to the laptop IP printed by `app.py` and `TOKEN` to your `SENSOR_TOKEN`. Wiring: a jumper wire from **D5 to GND** taped across the lid of the display case (a shoebox with a printed manuscript works for the demo). Opening the lid breaks the loop and an alert appears in the curator console.

### Arduino (accessibility buttons)
Remove the RESET-GND wire, then flash `firmware/arduino_buttons`. Make buttons from two strips of foil on cardboard with a small gap: one strip to the pin, the other to GND. **D2** repeats the last audio, **D3** changes language, **D4** goes to the next page or timeline event. Run `serial_bridge.py` on the laptop.

## 6. Demo checklist

- `python app.py` shows your documents and chunk counts on startup.
- The five demo questions you'll ask on camera all return cited answers. Ask them once in advance so the speech files are cached.
- The admin page shows the ESP8266 heartbeat as *closed*.
- An ESP32-CAM capture of a printed page returns readable text.
- The QR code opens on a phone connected to the same hotspot.
- Media files are in `backend/data/media` (credit every source; use only material you have the right to show).

## 7. Troubleshooting

| Problem | Fix |
|---|---|
| iPad can't open the kiosk | Same hotspot? Windows firewall allowed for Python on private networks? Use the IP printed by `app.py`, not `localhost`. |
| Mic button says a secure connection is needed | Expected over plain HTTP on the iPad. Use the laptop for voice or the cloudflared tunnel. |
| Answers say "AI assistant offline" | Check `LLM_PROVIDER`, `LLM_API_KEY` and `LLM_MODEL` in `.env`; the console prints the provider's error. |
| OCR returns almost nothing | Better light, page filling the frame, correct page language; check the Tesseract language packs. |
| ESP32-CAM "Camera init failed" | Reseat the ribbon cable, use a 5 V 2 A supply. |
| No audio on the iPad | Tap the screen once first; iOS only allows sound after a touch. |
| Library empty after ingesting | Restart `app.py` after running `ingest.py`. |

## 8. From prototype to production

| Prototype | Production at the Centre |
|---|---|
| ChromaDB | PostgreSQL with pgvector, replicated |
| Hosted LLM API | On-premise GPU server running an open model, so archive data stays in India |
| Web translator + gTTS | Bhashini (MeitY) or IndicTrans2 and Indic TTS models |
| ESP32-CAM | Overhead planetary book scanner for fragile manuscripts |
| Wire-loop tamper node | Case sensors plus temperature/humidity (DHT22/BME280) and lux monitoring for preservation |
| Local files | Object storage with 3-2-1 backups, PDF/A and TIFF masters, OAIS workflow, IIIF image viewer |
| iPad / laptop | Commercial 32–43" touch kiosks and 55–65" wall displays |
| Admin PIN | Role-based accounts for curators, researchers and administrators |

The timeline and knowledge map are curated demo data. Verify every entry against Dr. Ambedkar Foundation sources before public use.
