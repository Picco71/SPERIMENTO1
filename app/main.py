# -*- coding: utf-8 -*-
"""GPS Track Video - desktop Windows.
Crea video da traccia GPS + foto/video geolocalizzati con mappa 3D."""
import json, os, re, subprocess, sys, tempfile, uuid
import numpy as np
import imageio_ffmpeg
from PIL import Image

from PySide6.QtCore import (Qt, QMimeData, QObject, QThread, QTimer,
                            Signal, Slot, QEventLoop, QUrl)
from PySide6.QtGui import QColor, QDrag, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QColorDialog, QComboBox,
    QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QTabWidget, QVBoxLayout, QWidget)

from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebChannel import QWebChannel

APP_DIR = os.path.dirname(os.path.abspath(__file__))
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

# ------------------------------------------------------------------ dati
class TrackPoint:
    def __init__(self, lat, lon, t=0.0):
        self.lat, self.lon, self.t = lat, lon, t

class Track:
    def __init__(self, name, points):
        self.name, self.points = name, points

TRANSITIONS = ["NONE", "CROSSFADE", "SLIDE_LEFT", "SLIDE_UP", "ZOOM"]
XFADE_MAP = {"CROSSFADE": "fade", "SLIDE_LEFT": "slideleft",
             "SLIDE_UP": "slideup", "ZOOM": "circleopen"}

class Item:
    def __init__(self, id, path, is_video, title=""):
        self.id = id
        self.path = path
        self.is_video = is_video
        self.title = title
        self.comment = ""
        self.color = "#CC000000"
        self.duration = 3.0          # secondi (foto)
        self.lat = None
        self.lon = None
        self.transition = "CROSSFADE"

STATE = {"track": None, "items": [], "music": None,
         "sync_beat": False, "bpm": 120.0}

# ------------------------------------------------------- import tracce
def parse_track(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".gpx":
        return parse_gpx(path)
    if ext == ".kml":
        return parse_kml(path)
    if ext in (".json", ".geojson"):
        return parse_geojson(path)
    raise ValueError("Formato non supportato: usa .gpx .kml .geojson")

def parse_gpx(path):
    tree = ET_parse(path)
    pts = []
    for el in tree.iter():
        if el.tag.endswith("trkpt"):
            lat = float(el.get("lat")); lon = float(el.get("lon"))
            t = 0.0
            for ch in el:
                if ch.tag.endswith("time") and ch.text:
                    t = ch.text
            pts.append(TrackPoint(lat, lon))
    return Track(os.path.basename(path), pts)

def parse_kml(path):
    tree = ET_parse(path)
    pts = []
    for el in tree.iter():
        if el.tag.endswith("coordinates") and el.text:
            for tup in el.text.strip().split():
                p = tup.split(",")
                if len(p) >= 2:
                    pts.append(TrackPoint(float(p[1]), float(p[0])))
    return Track(os.path.basename(path), pts)

def parse_geojson(path):
    data = json.load(open(path, encoding="utf-8"))
    coords = []
    def walk(o):
        if isinstance(o, list):
            if len(o) >= 2 and all(isinstance(x, (int, float)) for x in o[:2]):
                coords.append(TrackPoint(o[1], o[0]))
            else:
                for x in o:
                    walk(x)
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
    walk(data)
    return Track(os.path.basename(path), coords)

def ET_parse(path):
    import xml.etree.ElementTree as ET
    return ET.parse(path)

# ------------------------------------------------------------- EXIF GPS
def exif_gps(path):
    try:
        img = Image.open(path)
        exif = img.getexif()
        ifd = exif.get_ifd(0x8825)
        if not ifd:
            return None
        def conv(dms, ref):
            vals = []
            for d in dms:
                v = float(d.numerator) / float(d.denominator) \
                    if hasattr(d, "numerator") else float(d)
                vals.append(v)
            val = vals[0] + vals[1] / 60 + vals[2] / 3600
            return -val if ref in ("S", "W") else val
        return conv(ifd[2], ifd[1]), conv(ifd[4], ifd[3])
    except Exception:
        return None

def nearest_point(track, lat, lon):
    best, bd = 0, float("inf")
    for i, p in enumerate(track.points):
        d = (p.lat - lat) ** 2 + (p.lon - lon) ** 2
        if d < bd:
            bd, best = d, i
    return best

# ----------------------------------------------------- durate / BPM
def ffprobe_duration(path):
    p = subprocess.run([FFMPEG, "-i", path], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", p.stderr)
    if not m:
        return 5.0
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))

def detect_bpm(path):
    p = subprocess.run([FFMPEG, "-i", path, "-f", "s16le", "-ac", "1",
                        "-ar", "22050", "-"], capture_output=True)
    pcm = np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32)
    if pcm.size < 22050:
        return 120.0
    win = 2205  # 100ms
    n = pcm.size // win
    e = np.abs(pcm[: n * win]).reshape(n, win).mean(axis=1)
    thr = max(e.mean() * 1.6, 300)
    peaks = [i for i in range(1, n - 1)
             if e[i] > thr and e[i] >= e[i-1] and e[i] >= e[i+1]]
    if len(peaks) < 4:
        return 120.0
    intervals = np.diff(peaks) * 0.1
    bpm = 60.0 / max(float(intervals.mean()), 0.01)
    while bpm > 200:
        bpm /= 2
    while bpm < 60:
        bpm *= 2
    return round(float(bpm), 1)

def snap_duration(sec, bpm):
    beat = 60.0 / bpm
    return max(beat, round(sec / beat) * beat)

def esc_drawtext(t):
    for ch, rep in (("\\", "\\\\"), (":", "\\:"), ("'", "\\'"),
                    (",", "\\,"), ("%", "\\%")):
        t = t.replace(ch, rep)
    return t

# ------------------------------------------------------------ mappa 3D
MAP_HTML = """
<!DOCTYPE html><html><head><meta charset="utf-8">
<link href="https://cdn.jsdelivr.net/npm/maplibre-gl@4.7.1/dist/maplibre-gl.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<style>html,body,#map{margin:0;height:100%;width:100%}</style>
</head><body><div id="map"></div><script>
var bridge = null;
var trackGeojson = null;
var markers = [];
var STYLES = {
  OSM: {version:8, sources:{b:{type:'raster',
    tiles:['https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png'],
    tileSize:256, attribution:'(c) OpenStreetMap (c) CARTO'}},
    layers:[{id:'bg',type:'raster',source:'b'}]},
  TOPO: {version:8, sources:{b:{type:'raster',
    tiles:['https://a.tile.opentopomap.org/{z}/{x}/{y}.png'],
    tileSize:256, attribution:'(c) OpenStreetMap (c) OpenTopoMap'}},
    layers:[{id:'bg',type:'raster',source:'b'}]},
  DARK: {version:8, sources:{b:{type:'raster',
    tiles:['https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png'],
    tileSize:256, attribution:'(c) OpenStreetMap (c) CARTO'}},
    layers:[{id:'bg',type:'raster',source:'b'}]}
};
var map = new maplibregl.Map({container:'map', style:STYLES.OSM,
  center:[9.19,45.46], zoom:12, pitch:0});
map.on('load', function(){ addLayers(); });
map.on('style.load', function(){ addLayers(); });

function addLayers(){
  if (trackGeojson) {
    if (map.getSource('track')) { map.removeLayer('track-line'); map.removeSource('track'); }
    map.addSource('track',{type:'geojson',data:trackGeojson});
    map.addLayer({id:'track-line',type:'line',source:'track',
      paint:{'line-color':'#FF3D00','line-width':4}});
  }
  if (map.getSource('media')) { map.removeLayer('media-pts'); map.removeSource('media'); }
  map.addSource('media',{type:'geojson',data:{type:'FeatureCollection',features:markers}});
  map.addLayer({id:'media-pts',type:'circle',source:'media',
    paint:{'circle-radius':8,'circle-color':'#2962FF',
           'circle-stroke-color':'#fff','circle-stroke-width':2}});
}
function setTrack(gj){ trackGeojson=JSON.parse(gj); addLayers();
  var b=new maplibregl.LngLatBounds();
  trackGeojson.geometry.coordinates.forEach(function(p){b.extend(p)});
  map.fitBounds(b,{padding:60}); }
function setMarkers(j){ markers=JSON.parse(j); addLayers(); }
function setStyleByName(n){ map.setStyle(STYLES[n]); }
function setPitch(on){ map.setPitch(on?60:0); map.setBearing(on?40:0); }
function centerOn(lat,lon,zoom){ map.jumpTo({center:[lon,lat],zoom:zoom||15,pitch:60,bearing:35}); }
function dropItemAt(x,y,id){
  var ll = map.unproject([x,y]);
  if (bridge) bridge.itemDropped(id, ll.lat, ll.lng);
}
new QWebChannel(qt.webChannelTransport, function(ch){ bridge = ch.objects.bridge; });
</script></body></html>
"""

class MapBridge(QObject):
    dropped = Signal(str, float, float)

    @Slot(str, float, float)
    def itemDropped(self, item_id, lat, lng):
        self.dropped.emit(item_id, lat, lng)

class MapView(QWebEngineView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.bridge = MapBridge()
        self.channel = QWebChannel(self.page())
        self.channel.registerObject("bridge", self.bridge)
        self.page().setWebChannel(self.channel)
        self.setHtml(MAP_HTML, QUrl("https://local/"))

    def set_style(self, name):
        self.page().runJavaScript("setStyleByName('%s')" % name)

    def set_3d(self, on):
        self.page().runJavaScript("setPitch(%s)" % ("true" if on else "false"))

    def draw_track(self, track):
        gj = {"type": "Feature", "properties": {},
              "geometry": {"type": "LineString",
              "coordinates": [[p.lon, p.lat] for p in track.points]}}
        self.page().runJavaScript("setTrack('%s')" % json.dumps(gj))

    def draw_markers(self):
        feats = [{"type": "Feature", "properties": {},
                  "geometry": {"type": "Point", "coordinates": [it.lon, it.lat]}}
                 for it in STATE["items"] if it.lat is not None]
        self.page().runJavaScript("setMarkers('%s')" % json.dumps(feats))

    def snapshot(self, lat, lon, w, h, out_path):
        """Cattura uno sfondo mappa 3D (pitch 60) per una coordinata.
        NOTA: va chiamata dal thread GUI."""
        view = QWebEngineView()
        view.resize(w, h)
        view.setAttribute(Qt.WA_DontShowOnScreen, True)
        vb = MapBridge()
        ch = QWebChannel(view.page())
        ch.registerObject("bridge", vb)
        view.page().setWebChannel(ch)
        loop = QEventLoop()
        view.loadFinished.connect(lambda *_: QTimer.singleShot(3000, loop.quit))
        view.setHtml(MAP_HTML, QUrl("https://local/"))
        loop.exec()
        view.page().runJavaScript("centerOn(%f,%f,15)" % (lat, lon))
        loop2 = QEventLoop()
        QTimer.singleShot(2500, loop2.quit)
        loop2.exec()
        ok = view.grab().save(out_path, "PNG")
        view.deleteLater()
        return ok

class MapDropView(MapView):
    """Mappa che accetta il drop degli elementi media (drag dalla lista)."""
    def dragEnterEvent(self, e):
        if e.mimeData().hasText() and e.mimeData().text().startswith("GPSID:"):
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dropEvent(self, e):
        txt = e.mimeData().text()
        if txt.startswith("GPSID:"):
            e.acceptProposedAction()
            pos = e.position().toPoint()
            item_id = txt.split(":", 1)[1]
            self.page().runJavaScript("dropItemAt(%d,%d,'%s')"
                                      % (pos.x(), pos.y(), item_id))
        else:
            super().dropEvent(e)

class DraggableList(QListWidget):
    """Lista media: i suoi elementi si trascinano sulla mappa."""
    def startDrag(self, actions):
        item = self.currentItem()
        if not item:
            return
        mime = QMimeData()
        mime.setText("GPSID:" + item.data(Qt.UserRole))
        drag = QDrag(self)
        drag.setMimeData(mime)
        pix = QPixmap(120, 80)
        pix.fill(QColor("#2962FF"))
        drag.setPixmap(pix)
        drag.exec(Qt.CopyAction | Qt.MoveAction)

SUGGESTED = ["YouTube Audio Library (royalty-free)",
             "Free Music Archive - FMA", "Pixabay Music",
             "Mixkit (tracce gratuite)", "MusOpen (classica, pubblico dominio)"]

class EditDialog(QDialog):
    def __init__(self, item, parent=None):
        super().__init__(parent)
        self.item = item
        self.setWindowTitle("Modifica elemento")
        f = QFormLayout(self)
        self.ed_title = QLineEdit(item.title)
        self.ed_comment = QLineEdit(item.comment)
        self.spin_dur = QDoubleSpinBox()
        self.spin_dur.setRange(0.5, 60)
        self.spin_dur.setValue(item.duration)
        self.spin_dur.setSuffix(" s")
        self.btn_color = QPushButton(item.color)
        self.btn_color.setStyleSheet("background:%s;color:white" % item.color)
        self.cmb_tr = QComboBox()
        self.cmb_tr.addItems(TRANSITIONS)
        self.cmb_tr.setCurrentText(item.transition)
        f.addRow("Titolo", self.ed_title)
        f.addRow("Commento (sovrapposto al video)", self.ed_comment)
        f.addRow("Colore sfondo commento", self.btn_color)
        f.addRow("Durata (foto)", self.spin_dur)
        f.addRow("Transizione uscita", self.cmb_tr)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)
        self.btn_color.clicked.connect(self.pick_color)

    def pick_color(self):
        c = QColorDialog.getColor(QColor(self.item.color), self)
        if c.isValid():
            self.item.color = c.name(QColor.HexArgb).upper()
            self.btn_color.setText(self.item.color)
            self.btn_color.setStyleSheet(
                "background:%s;color:white" % self.item.color)

    def accept(self):
        it = self.item
        it.title = self.ed_title.text()
        it.comment = self.ed_comment.text()
        it.duration = self.spin_dur.value()
        it.transition = self.cmb_tr.currentText()
        super().accept()

# ---------------------------------------------------------------- export
class ExportWorker(QThread):
    progress = Signal(int, str)
    done = Signal(str)
    failed = Signal(str)
    request_snapshot = Signal(float, float, str)   # lat, lon, out_path (-> GUI)

    def __init__(self, preset, out_path, with_map_bg):
        super().__init__()
        self.w, self.h = preset
        self.out_path = out_path
        self.with_map_bg = with_map_bg
        self.tmp = tempfile.mkdtemp(prefix="gpsvideo_")
        self._snap_result = None

    def run(self):
        try:
            items = STATE["items"]
            if not items:
                self.failed.emit("Nessun elemento nella timeline")
                return
            bpm = STATE["bpm"]
            total = len(items)
            clips = []
            snap_cache = {}
            for i, it in enumerate(items):
                self.progress.emit(int(100 * i / (total + 1)),
                                   "Clip %d/%d: %s" % (i + 1, total,
                                   it.title or os.path.basename(it.path)))
                dur = snap_duration(it.duration, bpm) \
                    if STATE["sync_beat"] else it.duration
                if it.is_video:
                    dur = ffprobe_duration(it.path)
                bg = None
                if self.with_map_bg and it.lat is not None:
                    key = (round(it.lat, 4), round(it.lon, 4))
                    if key not in snap_cache:
                        bgp = os.path.join(self.tmp, "map_%d.png" % len(snap_cache))
                        snap_cache[key] = bgp if self._map_snapshot(
                            it.lat, it.lon, bgp) else None
                    bg = snap_cache[key]
                clip = os.path.join(self.tmp, "clip_%03d.mp4" % i)
                self._render_item(it, clip, dur, bg)
                clips.append((clip, dur))
            self.progress.emit(99, "Unione finale con transizioni e musica...")
            self._assemble(clips)
            self.progress.emit(100, "Fatto")
            self.done.emit(self.out_path)
        except Exception as e:
            import traceback
            self.failed.emit("%s\n%s" % (e, traceback.format_exc()))

    def _map_snapshot(self, lat, lon, path):
        """Chiede lo snapshot al thread GUI e attende il risultato."""
        self._snap_result = None
        self.request_snapshot.emit(lat, lon, path)
        for _ in range(150):          # timeout 15s
            self.msleep(100)
            if self._snap_result is not None:
                break
        return bool(self._snap_result)

    def _render_item(self, it, out, dur, bg):
        W, H = self.w, self.h
        if it.is_video:
            base = ("scale=%d:%d:force_original_aspect_ratio=decrease,"
                    "pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30" % (W, H, W, H))
        else:
            base = ("zoompan=z='min(1+0.0012*on,1.15)':x='iw/2-(iw/zoom/2)':"
                    "y='ih/2-(ih/zoom/2)':d=%d:s=%dx%d:fps=30,setsar=1"
                    % (int(dur * 30), W, H))
        dt = ""
        if it.comment:
            c = QColor(it.color)
            bx = "0x%02X%02X%02X%02X" % (c.red(), c.green(), c.blue(), c.alpha())
            dt = (",drawtext=fontfile=C\\:/Windows/Fonts/arial.ttf:"
                  "text='%s':fontcolor=white:fontsize=%d:box=1:"
                  "boxcolor=%s:boxborderw=16:x=(w-text_w)/2:y=h*0.76"
                  % (esc_drawtext(it.comment), int(H * 0.045), bx))
        if bg:
            fc = ("[1:v]scale=%d:%d:force_original_aspect_ratio=increase,"
                  "crop=%d:%d[bg];[0:v]%s,scale=trunc(%d*0.74/2)*2:-2[m];"
                  "[bg][m]overlay=(W-w)/2:(H-h)/2%s[v]"
                  % (W, H, W, H, base, W, dt))
            pre = ["-i", it.path, "-loop", "1", "-t", str(dur), "-i", bg]
        else:
            fc = "%s%s[v]" % (base, dt)
            pre = ["-i", it.path]
        cmd = [FFMPEG, "-y"] + pre + ["-filter_complex", fc, "-map", "[v]",
              "-t", str(dur), "-c:v", "libx264", "-preset", "veryfast",
              "-crf", "20", "-pix_fmt", "yuv420p", out]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if not os.path.exists(out):
            raise RuntimeError("ffmpeg clip fallito: " + r.stderr[-400:])

    def _assemble(self, clips):
        overlap = 0.6
        all_none = all(it.transition == "NONE" for it in STATE["items"])
        music = STATE["music"]
        cmd = [FFMPEG, "-y"]
        for c, _ in clips:
            cmd += ["-i", c]
        if music:
            cmd += ["-i", music]
        if all_none or len(clips) == 1:
            n = len(clips)
            fc = "".join("[%d:v]" % i for i in range(n)) + \
                 "concat=n=%d:v=1:a=0[v]" % n
            tail = ["-filter_complex", fc, "-map", "[v]"]
        else:
            parts, cur, offset = [], None, 0.0
            for i in range(len(clips) - 1):
                tr = STATE["items"][i].transition
                kind = XFADE_MAP.get(tr, "fade")
                offset += clips[i][1] - overlap
                a = cur if cur else "[0:v]"
                lbl = "[xf%d]" % i
                parts.append("%s[%d:v]xfade=transition=%s:duration=%.3f:"
                             "offset=%.3f%s" % (a, i + 1, kind, overlap,
                             offset, lbl))
                cur = lbl
            tail = ["-filter_complex", ";".join(parts), "-map", cur]
        cmd += tail
        if music:
            cmd += ["-map", "%d:a:0" % len(clips), "-c:a", "aac",
                    "-b:a", "192k", "-shortest"]
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", self.out_path]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if not os.path.exists(self.out_path):
            raise RuntimeError("ffmpeg finale fallito: " + r.stderr[-600:])

# ------------------------------------------------------------------- GUI
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GPS Track Video")
        self.resize(1100, 760)
        tabs = QTabWidget()
        self.setCentralWidget(tabs)
        tabs.addTab(self.tab_track(), "Traccia")
        tabs.addTab(self.tab_media(), "Media")
        tabs.addTab(self.tab_timeline(), "Timeline")
        tabs.addTab(self.tab_export(), "Export")

    # ---------------- tab 1: traccia + mappa
    def tab_track(self):
        w = QWidget(); v = QVBoxLayout(w)
        self.btn_track = QPushButton("Carica traccia (GPX / KML / GeoJSON)")
        self.btn_track.clicked.connect(self.load_track)
        self.lbl_track = QLabel("Nessuna traccia caricata")
        self.chk_snap = QCheckBox("Aggancia i punti alla traccia")
        self.chk_snap.setChecked(True)
        self.map_w = MapDropView()
        self.map_w.bridge.dropped.connect(self.on_drop)
        v.addWidget(self.btn_track); v.addWidget(self.lbl_track)
        v.addWidget(self.chk_snap); v.addWidget(self.map_w, 1)
        return w

    def load_track(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "Traccia GPS", "",
            "Tracce GPS (*.gpx *.kml *.json *.geojson)")
        if not p:
            return
        try:
            tr = parse_track(p)
            if not tr.points:
                raise ValueError("Traccia vuota")
            STATE["track"] = tr
            self.lbl_track.setText("%s - %d punti" % (tr.name, len(tr.points)))
            self.map_w.draw_track(tr)
        except Exception as e:
            QMessageBox.critical(self, "Errore", str(e))

    def on_drop(self, item_id, lat, lng):
        it = next((x for x in STATE["items"] if x.id == item_id), None)
        if not it:
            return
        if self.chk_snap.isChecked() and STATE["track"]:
            p = STATE["track"].points[nearest_point(STATE["track"], lat, lng)]
            lat, lng = p.lat, p.lon
        it.lat, it.lon = lat, lng
        self.map_w.draw_markers()
        self.refresh_lists()

    # ---------------- tab 2: media
    def tab_media(self):
        w = QWidget(); v = QVBoxLayout(w)
        self.btn_media = QPushButton("Aggiungi foto e video")
        self.btn_media.clicked.connect(self.load_media)
        tip = QLabel("Tieni premuto un elemento e trascinalo sulla mappa "
                     "(tab Traccia) per posizionarlo dove manca il GPS.")
        self.media_list = DraggableList()
        self.media_list.setDragEnabled(True)
        self.media_list.setAcceptDrops(False)
        v.addWidget(self.btn_media); v.addWidget(tip)
        v.addWidget(self.media_list, 1)
        return w

    def load_media(self):
        ps, _ = QFileDialog.getOpenFileNames(
            self, "Foto e video", "",
            "Media (*.jpg *.jpeg *.png *.mp4 *.mov *.avi *.mkv *.webm)")
        for p in ps:
            gps = exif_gps(p) if p.lower().endswith((".jpg", ".jpeg")) else None
            it = Item(uuid.uuid4().hex, p,
                      p.lower().endswith((".mp4", ".mov", ".avi", ".mkv", ".webm")),
                      os.path.splitext(os.path.basename(p))[0])
            if gps:
                it.lat, it.lon = gps
            STATE["items"].append(it)
        self.refresh_lists()
        self.map_w.draw_markers()

    # ---------------- tab 3: timeline
    def tab_timeline(self):
        w = QWidget(); v = QVBoxLayout(w)
        h = QHBoxLayout()
        b1 = QPushButton("Transizione uguale per tutti")
        b2 = QPushButton("Transizioni random")
        b1.clicked.connect(self.all_same)
        b2.clicked.connect(self.all_random)
        h.addWidget(b1); h.addWidget(b2)
        self.tl_list = QListWidget()
        self.tl_list.itemDoubleClicked.connect(self.edit_item)
        self.tl_list.setDragDropMode(QListWidget.InternalMove)
        self.tl_list.model().rowsMoved.connect(self.sync_order)
        v.addLayout(h); v.addWidget(self.tl_list, 1)
        return w

    def sync_order(self, *args):
        """Sincronizza l'ordine di STATE con il riordino visivo della lista."""
        ids = [self.tl_list.item(i).data(Qt.UserRole)
               for i in range(self.tl_list.count())]
        by_id = {it.id: it for it in STATE["items"]}
        STATE["items"] = [by_id[i] for i in ids if i in by_id]
        self.refresh_lists()

    def all_same(self):
        d = QDialog(self); d.setWindowTitle("Scegli transizione")
        f = QFormLayout(d)
        cmb = QComboBox(); cmb.addItems(TRANSITIONS)
        f.addRow(cmb)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept); bb.rejected.connect(d.reject)
        f.addRow(bb)
        if d.exec() == QDialog.Accepted:
            tr = cmb.currentText()
            for it in STATE["items"]:
                it.transition = tr
            self.refresh_lists()

    def all_random(self):
        import random
        for it in STATE["items"]:
            it.transition = random.choice(TRANSITIONS)
        self.refresh_lists()

    def edit_item(self, lw_item):
        it = next((x for x in STATE["items"]
                   if x.id == lw_item.data(Qt.UserRole)), None)
        if it and EditDialog(it, self).exec() == QDialog.Accepted:
            self.refresh_lists()

    # ---------------- tab 4: export
    def tab_export(self):
        w = QWidget(); v = QVBoxLayout(w)
        self.cmb_style = QComboBox(); self.cmb_style.addItems(["OSM", "TOPO", "DARK"])
        self.cmb_style.currentTextChanged.connect(self.map_w.set_style)
        self.chk_3d = QCheckBox("Visuale 3D (pitch 60 gradi)")
        self.chk_3d.toggled.connect(self.map_w.set_3d)
        self.cmb_preset = QComboBox()
        self.cmb_preset.addItem("Reels / TikTok / Shorts (1080x1920)", (1080, 1920))
        self.cmb_preset.addItem("YouTube (1920x1080)", (1920, 1080))
        self.cmb_preset.addItem("Post quadrato (1080x1080)", (1080, 1080))
        self.chk_mapbg = QCheckBox(
            "Sfondo mappa 3D sotto ogni media (richiede internet)")
        self.chk_mapbg.setChecked(True)
        self.btn_music = QPushButton("Scegli musica (file)")
        self.btn_music.clicked.connect(self.pick_music)
        self.btn_sug = QPushButton("Brani gratuiti consigliati")
        self.btn_sug.clicked.connect(self.show_suggested)
        self.lbl_music = QLabel("Musica: nessuna")
        self.lbl_bpm = QLabel("BPM: non rilevati")
        self.chk_sync = QCheckBox(
            "Sincronizza durate e transizioni con i battiti (automatico)")
        self.btn_export = QPushButton("ESPORTA VIDEO")
        self.btn_export.setStyleSheet("font-weight:bold;font-size:15px;padding:10px")
        self.btn_export.clicked.connect(self.do_export)
        self.prog = QProgressBar(); self.prog.setVisible(False)
        self.lbl_status = QLabel("")
        for x in (QLabel("Stile mappa (gratuito)"), self.cmb_style, self.chk_3d,
                  QLabel("Formato output"), self.cmb_preset, self.chk_mapbg,
                  self.btn_music, self.btn_sug, self.lbl_music, self.lbl_bpm,
                  self.chk_sync, self.btn_export, self.prog, self.lbl_status):
            v.addWidget(x)
        v.addStretch(1)
        return w

    def pick_music(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "Musica", "", "Audio (*.mp3 *.wav *.m4a *.aac *.ogg *.flac)")
        if not p:
            return
        STATE["music"] = p
        self.lbl_music.setText("Musica: " + os.path.basename(p))
        self.lbl_status.setText("Analisi battiti in corso...")
        QApplication.processEvents()
        STATE["bpm"] = detect_bpm(p)
        self.lbl_bpm.setText("BPM rilevati: %.0f" % STATE["bpm"])

    def show_suggested(self):
        QMessageBox.information(
            self, "Librerie gratuite / royalty-free",
            "I brani di Instagram/TikTok non sono scaricabili liberamente.\n\n"
            "Fonti consigliate:\n - " + "\n - ".join(SUGGESTED) +
            "\n\nScarica il brano e usalo con 'Scegli musica (file)'.")

    def on_snapshot_request(self, lat, lon, path):
        """Eseguito nel thread GUI: cattura lo snapshot mappa per l'export."""
        ok = False
        try:
            ok = self.map_w.snapshot(lat, lon, 1280, 720, path)
        except Exception:
            ok = False
        self.worker._snap_result = ok

    def do_export(self):
        if not STATE["items"]:
            QMessageBox.warning(self, "Attenzione", "Aggiungi prima dei media")
            return
        STATE["sync_beat"] = self.chk_sync.isChecked()
        out, _ = QFileDialog.getSaveFileName(
            self, "Salva video", "gps_video.mp4", "Video MP4 (*.mp4)")
        if not out:
            return
        preset = self.cmb_preset.currentData()
        self.worker = ExportWorker(preset, out, self.chk_mapbg.isChecked())
        self.worker.progress.connect(
            lambda p, m: (self.prog.setValue(p), self.lbl_status.setText(m)))
        self.worker.done.connect(self.on_export_done)
        self.worker.failed.connect(self.on_export_failed)
        self.worker.request_snapshot.connect(self.on_snapshot_request)
        self.prog.setVisible(True)
        self.prog.setValue(0)
        self.worker.start()

    def on_export_done(self, path):
        self.prog.setVisible(False)
        QMessageBox.information(self, "Fatto", "Video salvato:\n%s" % path)

    def on_export_failed(self, msg):
        self.prog.setVisible(False)
        QMessageBox.critical(self, "Errore export", msg)

    # ---------------- liste
    def refresh_lists(self):
        self.media_list.clear()
        self.tl_list.clear()
        for i, it in enumerate(STATE["items"]):
            gps = "%.5f,%.5f" % (it.lat, it.lon) if it.lat is not None \
                  else "senza GPS (trascina sulla mappa)"
            t = "[VIDEO]" if it.is_video else "[FOTO]"
            m = QListWidgetItem("%s %s - %s" % (t, it.title, gps))
            m.setData(Qt.UserRole, it.id)
            self.media_list.addItem(m)
            c = QColor(it.color)
            txt = "%d. %s | %.1fs | %s" % (i + 1, it.title, it.duration,
                                           it.transition)
            if it.comment:
                txt += " | %s" % it.comment
            t2 = QListWidgetItem(txt)
            t2.setData(Qt.UserRole, it.id)
            t2.setBackground(c)
            t2.setForeground(QColor("white") if c.lightness() < 130
                             else QColor("black"))
            self.tl_list.addItem(t2)

def main():
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
