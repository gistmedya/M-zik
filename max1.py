#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MİMARİ (RTMP kalıcı):

  [Okuyucu FFmpeg (her film için ayrı)]  --MPEG-TS pipe-->  [Çıkış FFmpeg (HİÇ KAPANMAZ)]  --RTMP-->  Sunucu

- Okuyucu: filmi açar, logo/yazı/altyazı ekler, x264+AAC ile kodlar, MPEG-TS olarak stdout'a yazar.
- Çıkış: stdin'den MPEG-TS okur, yeniden kodlamadan (-c copy) RTMP'ye gönderir.
- Film bitince sadece okuyucu kapanır; çıkış süreci ve RTMP bağlantısı açık kalır.
- Sadece RTMP/çıkış süreci gerçekten ölürse yeniden başlatılır.

ALTYAZI:
  M3U'da #EXTVLCOPT:sub-url=... satırı ile belirtilir. SRT / VTT / ASS / SSA desteklenir.
  VTT otomatik SRT'ye çevrilir. Forced altyazı için .forced.srt gibi dosyalar kullanılır.

"BİTTİ" KARARI:
  Return code 0 tek başına yeterli değildir. Kaynak erken EOF verirse FFmpeg yine 0 döner.
  Bu yüzden film ancak toplam süreye (END_TOLERANCE_SECONDS payıyla) ulaşıldıysa bitmiş sayılır.
  Süre okunamadıysa da en az 60 saniye kesintisiz yayın yapılmış olması şartı aranır.
"""

import gzip
import json
import os
import re
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urljoin

import requests

# ===================== AYARLAR =====================
RTMP_URL = "rtmp://ssh101.bozztv.com:1935/ssh101"
STREAM_KEY = os.getenv("STREAM_KEY") or "megatv_@"
RTMP_SERVER = f"{RTMP_URL}/{STREAM_KEY}"

M3U_URL = os.getenv("M3U_URL") or "https://raw.githubusercontent.com/ino8090/0101/refs/heads/main/deneme.m3u"
LOGO_URL = os.getenv("LOGO_URL") or "https://raw.githubusercontent.com/ino8090/0101/refs/heads/main/1791642450475.png"

STATE_FILE_NAME = os.getenv("STATE_FILE_NAME", "mega.json")
GITHUB_STEP_SUMMARY = os.getenv("GITHUB_STEP_SUMMARY")

STREAM_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
STREAM_REFERER = "https://vidmody.com/"
STREAM_ORIGIN = "https://vidmody.com"

LOGO_OPACITY = float(os.getenv("LOGO_OPACITY", "1.0"))
TEXT_OPACITY = float(os.getenv("TEXT_OPACITY", "1.0"))
BOLD_FONT_PATH = os.getenv("BOLD_FONT_PATH", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")

DECODER_THREADS = os.getenv("DECODER_THREADS", "1")

# İlerleme (time=) bu kadar sn gelmezse süreç donmuş sayılır.
WATCHDOG_TIMEOUT_SECONDS = int(os.getenv("WATCHDOG_TIMEOUT_SECONDS", "45"))
# İlk kare gelene kadar tanınan süre (HLS kaynaklar geç açılabilir).
WATCHDOG_STARTUP_SECONDS = int(os.getenv("WATCHDOG_STARTUP_SECONDS", "120"))

# Link değişip aynı film olduğunda geriden devam edilecek süre.
LINK_CHANGE_REWIND_SECONDS = int(os.getenv("LINK_CHANGE_REWIND_SECONDS", "15"))

# Filmin bittiği sayılması için toplam süreden en fazla bu kadar sn eksik kalınabilir.
END_TOLERANCE_SECONDS = int(os.getenv("END_TOLERANCE_SECONDS", "90"))

# Art arda erken/hatalı bitişlerde seek konumu bu kadar geri alınır (seek sorunlarına karşı).
SEEK_BACKOFF_SECONDS = int(os.getenv("SEEK_BACKOFF_SECONDS", "30"))
SEEK_BACKOFF_AFTER_FAILURES = int(os.getenv("SEEK_BACKOFF_AFTER_FAILURES", "2"))

# Bu kadar art arda hızlı hatadan sonra sıradaki içeriğe geçilir.
MAX_CONSECUTIVE_FAILURES = int(os.getenv("MAX_CONSECUTIVE_FAILURES", "4"))
FAST_FAIL_THRESHOLD_SECONDS = 20
MAX_RETRY_DELAY_SECONDS = 120

# ÇİFT URL ("video;ses") AYARLARI
DUAL_FALLBACK_AFTER_FAILURES = int(os.getenv("DUAL_FALLBACK_AFTER_FAILURES", "3"))
DUAL_AUDIO_SYNC = os.getenv("DUAL_AUDIO_SYNC", "1") == "1"

# ===================== ALTYAZI AYARLARI =====================
SUBTITLE_ENABLED = os.getenv("SUBTITLE_ENABLED", "1") == "1"
SUBTITLE_FONT_NAME = os.getenv("SUBTITLE_FONT_NAME", "DejaVu Sans")
SUBTITLE_FONT_SIZE = os.getenv("SUBTITLE_FONT_SIZE", "22")
SUBTITLE_PRIMARY_COLOR = os.getenv("SUBTITLE_PRIMARY_COLOR", "&H00FFFFFF")   # Beyaz (AABBGGRR)
SUBTITLE_OUTLINE_COLOR = os.getenv("SUBTITLE_OUTLINE_COLOR", "&H00000000")   # Siyah outline
SUBTITLE_BACK_COLOR = os.getenv("SUBTITLE_BACK_COLOR", "&H80000000")         # Yarı saydam siyah
SUBTITLE_OUTLINE = os.getenv("SUBTITLE_OUTLINE", "2")
SUBTITLE_SHADOW = os.getenv("SUBTITLE_SHADOW", "1")
SUBTITLE_MARGIN_V = os.getenv("SUBTITLE_MARGIN_V", "40")
SUBTITLE_BORDER_STYLE = os.getenv("SUBTITLE_BORDER_STYLE", "1")  # 1=outline+shadow, 3=opak kutu


# ===================== KALICI RTMP ÇIKIŞ SÜRECİ =====================
class RtmpOutput:
    """Tek FFmpeg süreci: stdin'den MPEG-TS alır, RTMP'ye kopyalar. Filmler arası KAPANMAZ."""

    def __init__(self, rtmp_url):
        self.rtmp_url = rtmp_url
        self.proc = None
        self.failed = threading.Event()
        self.stderr_tail = deque(maxlen=30)

    def start(self):
        self.stop(force=True)
        self.failed.clear()
        self.stderr_tail.clear()
        cmd = [
            'ffmpeg', '-hide_banner', '-loglevel', 'warning', '-nostats',
            '-fflags', '+genpts+igndts',
            '-analyzeduration', '20000000',
            '-probesize', '20000000',
            '-f', 'mpegts', '-i', 'pipe:0',
            '-map', '0:v:0', '-map', '0:a:0?',
            '-c', 'copy',
            # NOT: -bsf:v h264_mp4toannexb KALDIRILDI (header'ı bozuyordu)
            '-flvflags', 'no_duration_filesize',
            '-f', 'flv', self.rtmp_url,
        ]
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
        threading.Thread(target=self._drain_stderr, args=(self.proc,), daemon=True).start()
        print(f"🔌 Kalıcı RTMP çıkış süreci başlatıldı => {self.rtmp_url}")

    def _drain_stderr(self, proc):
        try:
            for raw in iter(proc.stderr.readline, b''):
                self.stderr_tail.append(raw.decode('utf-8', 'replace').rstrip())
        except Exception:
            pass

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def write(self, data):
        try:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError, AttributeError):
            self.failed.set()
            return False

    def stop(self, force=False):
        p = self.proc
        if p is None:
            return
        self.proc = None
        try:
            if force:
                p.kill()
            else:
                p.stdin.close()
        except Exception:
            pass
        try:
            p.wait(timeout=5)
        except Exception:
            try:
                p.kill()
                p.wait(timeout=5)
            except Exception:
                pass


# ===================== YARDIMCI FONKSİYONLAR =====================
def format_hms(total_seconds):
    total_seconds = max(0, int(total_seconds))
    return f"{total_seconds // 3600:02d}:{(total_seconds % 3600) // 60:02d}:{total_seconds % 60:02d}"


# URL -> süre (sn). Bağlantı kopup aynı film yeniden denendiğinde ffprobe tekrar çalıştırılmaz.
_DURATION_CACHE = {}


def get_hls_duration(url, depth=0):
    """HLS VOD listelerinde #EXTINF sürelerini toplayarak toplam süreyi hesaplar."""
    if depth > 2 or '.m3u8' not in url.lower():
        return 0.0
    try:
        headers = {'User-Agent': STREAM_USER_AGENT, 'Referer': STREAM_REFERER, 'Origin': STREAM_ORIGIN}
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            return 0.0
        lines = [l.strip() for l in response.text.splitlines() if l.strip()]
        if not lines or not lines[0].startswith('#EXTM3U'):
            return 0.0

        for i, l in enumerate(lines):
            if l.startswith('#EXT-X-STREAM-INF'):
                for nxt in lines[i + 1:]:
                    if not nxt.startswith('#'):
                        return get_hls_duration(urljoin(url, nxt), depth + 1)
                return 0.0

        if not any(l.startswith('#EXT-X-ENDLIST') for l in lines):
            return 0.0

        total = 0.0
        for l in lines:
            m = re.match(r'#EXTINF:([\d.]+)', l)
            if m:
                total += float(m.group(1))
        return total
    except Exception:
        return 0.0


def get_video_duration_ffprobe(video_url, retries=3, timeout=15):
    """PROFESYONEL FFPROBE SÜRE TESPİT MEKANİZMASI."""
    if video_url in _DURATION_CACHE:
        cached = _DURATION_CACHE[video_url]
        print(f"⚡ Süre önbellekten alındı: {cached:.1f} sn ({format_hms(cached)})")
        return cached

    hls_duration = get_hls_duration(video_url)
    if hls_duration > 0:
        _DURATION_CACHE[video_url] = hls_duration
        print(f"⚡ HLS playlist'ten süre okundu: {hls_duration:.1f} sn ({format_hms(hls_duration)})")
        return hls_duration

    stages = [
        (max(5, timeout // 2), 3000000),
        (timeout, 10000000),
        (timeout + 10, 20000000),
    ]

    def build_cmd(attempt):
        stage_timeout, stage_probe = stages[min(attempt, len(stages)) - 1]
        cmd = [
            'ffprobe',
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_format',
            '-show_streams',
            '-allowed_extensions', 'ALL',
            '-headers', f"User-Agent: {STREAM_USER_AGENT}\r\nReferer: {STREAM_REFERER}\r\nOrigin: {STREAM_ORIGIN}\r\n",
            '-rw_timeout', str(stage_timeout * 1000000),
            '-analyzeduration', str(stage_probe),
            '-probesize', str(stage_probe),
            video_url
        ]
        return cmd, stage_timeout + 5

    for attempt in range(1, retries + 1):
        try:
            print(f"⏱️ ffprobe analizi başlatılıyor (Deneme {attempt}/{retries})...")
            ffprobe_cmd, run_timeout = build_cmd(attempt)

            result = subprocess.run(
                ffprobe_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=run_timeout
            )

            if result.returncode != 0:
                if attempt < retries:
                    time.sleep(2)
                continue

            data = json.loads(result.stdout)

            if 'format' in data and 'duration' in data['format']:
                try:
                    duration = float(data['format']['duration'])
                    if duration > 0:
                        _DURATION_CACHE[video_url] = duration
                        print(f"✅ ffprobe ile süre okundu (Format): {duration:.1f} sn ({format_hms(duration)})")
                        return duration
                except (ValueError, TypeError):
                    pass

            if 'streams' in data and isinstance(data['streams'], list):
                for stream in data['streams']:
                    if 'duration' in stream:
                        try:
                            duration = float(stream['duration'])
                            if duration > 0:
                                _DURATION_CACHE[video_url] = duration
                                print(f"✅ ffprobe ile süre okundu (Stream): {duration:.1f} sn ({format_hms(duration)})")
                                return duration
                        except (ValueError, TypeError):
                            continue

        except subprocess.TimeoutExpired:
            print(f"⚠️ ffprobe zaman aşımına uğradı (Deneme {attempt}/{retries}).")
        except json.JSONDecodeError:
            print(f"⚠️ ffprobe çıktısı çözümlenemedi (Deneme {attempt}/{retries}).")
        except Exception as e:
            print(f"⚠️ ffprobe beklenmeyen hata: {e}")

        if attempt < retries:
            time.sleep(2)

    print("⚠️ ffprobe ile toplam süre okunamadı (Canlı yayın veya korumalı akış olabilir).")
    return 0.0


def get_local_state():
    """(index, seconds, url, title) döndürür."""
    if not os.path.exists(STATE_FILE_NAME):
        print("ℹ️ Yerel state dosyası bulunamadı, 0'dan başlanıyor.")
        return 0, 0, "", ""
    if os.path.getsize(STATE_FILE_NAME) == 0:
        print(f"⚠️ Yerel state dosyası boş ({STATE_FILE_NAME}), 0'dan başlanıyor.")
        return 0, 0, "", ""
    try:
        with open(STATE_FILE_NAME, "r", encoding="utf-8") as f:
            data = json.load(f)
        idx = data.get("last_index", 0)
        sec = data.get("last_seconds", 0)
        print(f"✅ Yerel state okundu ({STATE_FILE_NAME}) => İndeks: {idx}, Saniye: {sec}")
        return idx, sec, data.get("last_url", ""), data.get("last_title", "")
    except Exception as e:
        print(f"⚠️ Yerel state okuma hatası: {e}")
        return 0, 0, "", ""


def update_local_state(index, seconds, url="", title=""):
    """Atomik yazım."""
    tmp_name = STATE_FILE_NAME + ".tmp"
    try:
        data = {
            "last_index": int(index),
            "last_seconds": int(seconds),
            "last_url": url,
            "last_title": title,
        }
        with open(tmp_name, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_name, STATE_FILE_NAME)
        print(f"💾 Konum kaydedildi => İndeks: {index}, Saniye: {int(seconds)}")
    except Exception as e:
        print(f"⚠️ Yerel state yazma hatası: {e}")


def get_m3u_playlist(m3u_url):
    """
    M3U'yu çeker. #EXTVLCOPT:sub-url=... satırını da altyazı olarak kaydeder.
    Farklı yazımlar desteklenir: sub-url, subtitle, subs
    """
    try:
        headers = {'User-Agent': STREAM_USER_AGENT, 'Referer': STREAM_REFERER}
        response = requests.get(m3u_url, headers=headers, timeout=15)
        if response.status_code == 200:
            playlist = []
            pending_title = None
            pending_sub = None
            for raw_line in response.text.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith('#EXTINF'):
                    match = re.search(r',(.+)$', line)
                    pending_title = match.group(1).strip() if match else None
                # 🔥 Altyazı satırı: #EXTVLCOPT:sub-url=... veya #EXTSUB:...
                elif line.upper().startswith('#EXTVLCOPT:SUB-URL='):
                    pending_sub = line.split('=', 1)[1].strip()
                elif line.upper().startswith('#EXTVLCOPT:SUBTITLE='):
                    pending_sub = line.split('=', 1)[1].strip()
                elif line.upper().startswith('#EXTSUB:'):
                    pending_sub = line.split(':', 1)[1].strip()
                elif not line.startswith('#') and line.startswith('http'):
                    title = pending_title or os.path.basename(line.split('?')[0])
                    playlist.append({
                        "url": line,
                        "title": title,
                        "sub_url": pending_sub or ""
                    })
                    pending_title = None
                    pending_sub = None
            return playlist
    except Exception as e:
        print(f"⚠️ M3U çekme hatası: {e}")
    return [{"url": m3u_url, "title": os.path.basename(m3u_url), "sub_url": ""}]


def download_logo():
    try:
        response = requests.get(LOGO_URL, headers={'User-Agent': STREAM_USER_AGENT}, timeout=15)
        if response.status_code == 200 and len(response.content) > 0:
            with open('logo.png', 'wb') as f:
                f.write(response.content)
            print("✅ Logo başarıyla indirildi.")
    except Exception as e:
        print(f"⚠️ Logo indirme hatası: {e}")


def write_text_file(path, text):
    """drawtext reload=1 için atomik yazar."""
    tmp_name = path + ".tmp"
    try:
        with open(tmp_name, 'w', encoding='utf-8') as f:
            f.write(text)
        os.replace(tmp_name, path)
    except Exception as e:
        print(f"⚠️ {path} yazma hatası: {e}")


# ===================== ALTYAZI FONKSİYONLARI =====================
def vtt_to_srt(vtt_text):
    """Basit VTT → SRT dönüştürücü."""
    lines = vtt_text.splitlines()
    out = []
    counter = 1
    i = 0

    # WEBVTT başlığını ve meta satırlarını atla
    while i < len(lines):
        if '-->' in lines[i]:
            break
        i += 1

    while i < len(lines):
        line = lines[i]
        if '-->' in line:
            line = line.replace('.', ',')
            line = re.sub(r'\s+(align|position|line|size|region):\S+', '', line)
            out.append(str(counter))
            out.append(line)
            counter += 1
        elif line.strip() and not line.startswith('NOTE') and not line.startswith('STYLE'):
            clean = re.sub(r'<[^>]+>', '', line)
            out.append(clean)
        elif not line.strip() and out and out[-1] != '':
            out.append('')
        i += 1

    return '\n'.join(out)


def _find_subtitle_ext(url):
    """URL'den altyazı uzantısını bulur."""
    clean = url.lower().split('?')[0].split('#')[0]
    for e in ('.ass', '.ssa', '.vtt', '.srt'):
        if clean.endswith(e):
            return e
    return '.srt'


def _clean_subtitle_url(url):
    """M3U yazım hatalarını temizler: baştaki ':' , boşluk, tırnak."""
    if not url:
        return ""
    url = url.strip()
    url = url.lstrip(': \t\'"')
    url = url.rstrip(' \t\'"')
    return url


def download_subtitle(sub_url, target_path=None):
    """
    Altyazıyı indirir. SRT / VTT / ASS / SSA destekler.
    VTT ise otomatik SRT'ye çevirir. Başarılıysa dosya yolunu döner.
    """
    if not sub_url:
        return None

    sub_url = _clean_subtitle_url(sub_url)
    if not sub_url or not sub_url.startswith(('http://', 'https://')):
        print(f"⚠️ Geçersiz altyazı URL'si: {sub_url}")
        return None

    ext = _find_subtitle_ext(sub_url)
    if target_path is None:
        target_path = f"subtitle{ext}"

    try:
        headers = {
            'User-Agent': STREAM_USER_AGENT,
            'Referer': STREAM_REFERER,
            'Origin': STREAM_ORIGIN,
        }
        r = requests.get(sub_url, headers=headers, timeout=20)
        if r.status_code != 200 or not r.content:
            print(f"⚠️ Altyazı indirilemedi (HTTP {r.status_code}): {sub_url}")
            return None

        content = r.content

        # Gzip
        if content[:2] == b'\x1f\x8b' or sub_url.lower().endswith('.gz'):
            try:
                content = gzip.decompress(content)
                text_preview = content[:200].decode('utf-8', 'ignore')
                if '[Script Info]' in text_preview:
                    ext = '.ass'
                elif 'WEBVTT' in text_preview:
                    ext = '.vtt'
                else:
                    ext = '.srt'
                target_path = f"subtitle{ext}"
            except Exception as e:
                print(f"⚠️ Gzip açma hatası: {e}")
                return None

        # Encoding tespiti
        try:
            text = content.decode('utf-8')
        except UnicodeDecodeError:
            try:
                text = content.decode('iso-8859-9')
                print("ℹ️ Altyazı ISO-8859-9 (Türkçe) olarak çözüldü.")
            except UnicodeDecodeError:
                text = content.decode('cp1254', errors='replace')
                print("ℹ️ Altyazı CP1254 olarak çözüldü (bazı karakterler bozuk olabilir).")

        text = text.lstrip('\ufeff')

        # Format doğrulama
        is_ass = '[Script Info]' in text or '[Events]' in text
        is_vtt = text.lstrip().startswith('WEBVTT')
        is_srt = '-->' in text

        if not (is_ass or is_vtt or is_srt):
            print("⚠️ Altyazı dosyası geçerli değil (SRT/VTT/ASS değil).")
            return None

        # VTT → SRT dönüşümü (FFmpeg için en güvenli)
        if is_vtt:
            text = vtt_to_srt(text)
            target_path = "subtitle.srt"
            print("🔄 VTT → SRT dönüşümü yapıldı.")
            ext = '.srt'

        if is_ass:
            print("ℹ️ ASS/SSA formatı: Fontlar sistemde yoksa görünüm bozuk olabilir.")

        with open(target_path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)

        size_kb = os.path.getsize(target_path) / 1024
        line_count = text.count('\n')
        print(f"✅ Altyazı indirildi: {target_path} ({size_kb:.1f} KB, ~{line_count} satır, format: {ext})")
        return target_path

    except Exception as e:
        print(f"⚠️ Altyazı indirme hatası: {e}")
        return None


def cleanup_subtitle_files():
    """Önceki filmden kalan altyazı dosyalarını temizler."""
    for f in ("subtitle.srt", "subtitle.vtt", "subtitle.ass", "subtitle.ssa"):
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass


# ===================== DASHBOARD =====================
def print_dashboard(title, index, playlist_len, seconds, status="🟢 Yayında", has_sub=False):
    sub_icon = "💬" if has_sub else "🚫"
    print("┌" + "─" * 58 + "┐")
    print(f"│ 🎬 İçerik         : {title[:36]:<36} │")
    print(f"│ 🔢 Sıra           : {f'{index + 1}/{playlist_len}':<36} │")
    print(f"│ ⏱️  Geçen Süre     : {format_hms(seconds):<36} │")
    print(f"│ 📡 Durum          : {status:<36} │")
    print(f"│ {sub_icon} Altyazı        : {'Var (forced)' if has_sub else 'Yok':<36} │")
    print("└" + "─" * 58 + "┘")


def write_step_summary(title, index, playlist_len, seconds, status="🟢 Yayında", has_sub=False):
    if not GITHUB_STEP_SUMMARY:
        return
    try:
        content = (
            "## 📺 Canlı Yayın Durumu (Maxanimasyon)\n\n"
            "| Alan | Değer |\n"
            "|---|---|\n"
            f"| 🎬 Şu an oynayan içerik | {title} |\n"
            f"| 🔢 Playlist sırası | {index + 1} / {playlist_len} |\n"
            f"| ⏱️ Geçen süre | {format_hms(seconds)} (sa:dk:sn) |\n"
            f"| 📡 Durum | {status} |\n"
            f"| 💬 Altyazı | {'Var' if has_sub else 'Yok'} |\n"
            f"| 🕒 Son güncelleme | {time.strftime('%Y-%m-%d %H:%M:%S')} |\n"
        )
        with open(GITHUB_STEP_SUMMARY, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        print(f"⚠️ Step summary yazma hatası: {e}")


def iter_stderr_lines(stream):
    """FFmpeg stderr'ini hem \\r hem \\n ile böler."""
    fd = stream.fileno()
    buf = b''
    while True:
        chunk = os.read(fd, 4096)
        if not chunk:
            if buf.strip():
                yield buf.decode('utf-8', 'replace')
            return
        buf += chunk
        parts = re.split(rb'[\r\n]', buf)
        buf = parts.pop()
        for p in parts:
            if p:
                yield p.decode('utf-8', 'replace')


def pump_reader_to_output(reader, output):
    """
    Okuyucunun stdout'unu (MPEG-TS) kalıcı RTMP çıkışına aktarır.
    🔥 İlk chunk'ı biriktirir — SPS/PPS/header tam gelsin.
    """
    fd = reader.stdout.fileno()
    first = True
    while True:
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break

        # 🔥 İlk chunk'ı biriktir — SPS/PPS tam gelsin
        if first:
            if len(chunk) < 16384:
                buffered = chunk
                deadline = time.time() + 5
                while len(buffered) < 16384 and time.time() < deadline:
                    try:
                        more = os.read(fd, 65536)
                        if not more:
                            break
                        buffered += more
                    except OSError:
                        break
                chunk = buffered
                print(f"📦 İlk MPEG-TS paketi hazırlandı ({len(chunk)} byte)")
            first = False

        if not output.write(chunk):
            try:
                reader.kill()
            except Exception:
                pass
            break


# ===================== OKUYUCU KOMUTU =====================
HEADERS_ARG = (
    f"User-Agent: {STREAM_USER_AGENT}\r\n"
    f"Referer: {STREAM_REFERER}\r\n"
    f"Origin: {STREAM_ORIGIN}\r\n"
)


def split_dual_url(target_url):
    """'video;ses' biçimini ayrıştırır."""
    video_url, _, audio_url = target_url.partition(";")
    return video_url.strip(), audio_url.strip()


def get_total_duration(target_url):
    """Toplam süre: video URL'sinden, okunamazsa ses URL'sinden."""
    video_url, audio_url = split_dual_url(target_url)
    duration = get_video_duration_ffprobe(video_url)
    if duration > 0 or not audio_url:
        return duration
    print("ℹ️ Video süresi okunamadı, ses URL'sinden süre deneniyor...")
    return get_video_duration_ffprobe(audio_url)


def make_input_options(url):
    is_hls = '.m3u8' in url.lower()
    opts = [
        '-re',
        '-headers', HEADERS_ARG,
        '-protocol_whitelist', 'file,http,https,tcp,tls,crypto',
        '-err_detect', 'ignore_err',
        '-fflags', '+genpts+discardcorrupt',
        '-thread_queue_size', '1024',
        '-max_interleave_delta', '0',
        '-analyzeduration', '10000000',
        '-probesize', '10000000',
        '-reconnect', '1',
    ]
    if is_hls:
        opts += ['-allowed_extensions', 'ALL',
                 '-reconnect_streamed', '1',
                 '-reconnect_delay_max', '3']
    else:
        opts += ['-reconnect_at_eof', '1',
                 '-reconnect_streamed', '1',
                 '-reconnect_delay_max', '10']
    opts += ['-rw_timeout', '10000000', '-threads', DECODER_THREADS]
    return opts


def _escape_ffmpeg_path(path):
    """FFmpeg filter içinde güvenli yol."""
    return path.replace('\\', '\\\\').replace(':', '\\:').replace("'", r"\'")


def build_subtitle_style():
    """Altyazı stilini FFmpeg force_style formatında döner."""
    return (
        f"FontName={SUBTITLE_FONT_NAME},"
        f"FontSize={SUBTITLE_FONT_SIZE},"
        f"PrimaryColour={SUBTITLE_PRIMARY_COLOR},"
        f"OutlineColour={SUBTITLE_OUTLINE_COLOR},"
        f"BackColour={SUBTITLE_BACK_COLOR},"
        f"BorderStyle={SUBTITLE_BORDER_STYLE},"
        f"Outline={SUBTITLE_OUTLINE},"
        f"Shadow={SUBTITLE_SHADOW},"
        f"Alignment=2,"  # 2 = alt orta
        f"MarginV={SUBTITLE_MARGIN_V}"
    )


def build_reader_command(target_url, seek_seconds, video_only=False, subtitle_file=None):
    seek_args = ['-ss', str(int(seek_seconds))] if seek_seconds > 0 else []

    video_url, audio_url = split_dual_url(target_url)
    audio_filter = []

    if audio_url and not video_only:
        input_args = (
            make_input_options(video_url) + seek_args + ['-i', video_url] +
            make_input_options(audio_url) + seek_args + ['-i', audio_url]
        )
        audio_map = ['-map', '1:a:0?']
        logo_index = 2
        if DUAL_AUDIO_SYNC:
            audio_filter = ['-af', 'aresample=async=1:first_pts=0']
    else:
        input_args = make_input_options(video_url) + seek_args + ['-i', video_url]
        audio_map = ['-map', '0:a:0?']
        logo_index = 1

    title_drawtext = (
        f"drawtext=textfile='title.txt':reload=1:fontfile='{BOLD_FONT_PATH}':"
        f"fontcolor=white@{TEXT_OPACITY}:fontsize=25:x=51:y=h-th-51"
    )

    # 🔥 ALTYAZI VAR MI?
    has_subtitle = bool(
        SUBTITLE_ENABLED
        and subtitle_file
        and os.path.exists(subtitle_file)
    )

    # 🔥 base_scale'i duruma göre kur (çift virgül hatasını önler)
    if has_subtitle:
        safe_path = _escape_ffmpeg_path(subtitle_file)
        style = build_subtitle_style()
        print(f"💬 Altyazı videoya yakılacak: {subtitle_file}")
        base_scale = (
            '[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,'
            'pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=25,'
            f"subtitles='{safe_path}':force_style='{style}'"
            '[main];'
        )
    else:
        base_scale = (
            '[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,'
            'pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=25'
            '[main];'
        )

    has_logo = os.path.exists('logo.png') and os.path.getsize('logo.png') > 0
    if has_logo:
        logo_inputs = ['-i', 'logo.png']
        filter_str = (
            base_scale +
            f'[{logo_index}:v]scale=-2:85,format=rgba,colorchannelmixer=aa={LOGO_OPACITY}[logo1];'
            '[main][logo1]overlay=W-w-70:48[tmp1];'
            f'[tmp1]{title_drawtext}[v]'
        )
    else:
        logo_inputs = []
        filter_str = (
            base_scale +
            f'[main]{title_drawtext}[v]'
        )

    return (
        ['ffmpeg'] + input_args + logo_inputs + [
            '-filter_complex', filter_str,
            '-map', '[v]',
        ] + audio_map + audio_filter + [
            '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
            '-r', '25', '-b:v', '2500k', '-maxrate', '2500k', '-bufsize', '3000k',
            '-g', '50', '-keyint_min', '25', '-sc_threshold', '0',
            '-x264-params', 'repeat-headers=1:force-cfr=1',
            '-c:a', 'aac', '-b:a', '128k', '-ac', '2', '-ar', '44100',
            '-flush_packets', '1', '-muxdelay', '0', '-muxpreload', '0',
            '-mpegts_flags', '+resend_headers+initial_discontinuity',
            '-f', 'mpegts', 'pipe:1',
        ]
    )


# ===================== OKUYUCUYU ÇALIŞTIR =====================
@dataclass
class ReaderResult:
    returncode: int
    stream_seconds: float
    stderr_tail: deque = field(default_factory=lambda: deque(maxlen=40))
    output_broken: bool = False


def run_reader(command, output, base_seconds, total_duration_sec, ctx):
    """Okuyucu FFmpeg'i çalıştırır, ilerlemeyi izler, sonucu döndürür."""
    process = subprocess.Popen(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    pump_thread = threading.Thread(target=pump_reader_to_output, args=(process, output), daemon=True)
    pump_thread.start()

    now = time.time()
    last_save_time = now
    last_dashboard_time = now
    current_seconds = base_seconds
    stderr_tail = deque(maxlen=40)
    progress = [now, False]

    def watchdog():
        while process.poll() is None:
            time.sleep(5)
            limit = WATCHDOG_TIMEOUT_SECONDS if progress[1] else WATCHDOG_STARTUP_SECONDS
            if time.time() - progress[0] > limit:
                print(f"🚨 Watchdog: {limit} saniyedir ilerleme yok. Okuyucu zorla sonlandırılıyor.")
                try:
                    process.kill()
                except Exception as e:
                    print(f"⚠️ Watchdog sonlandırma hatası: {e}")
                break

    threading.Thread(target=watchdog, daemon=True).start()

    for line in iter_stderr_lines(process.stderr):
        stderr_tail.append(line.rstrip())
        if "time=" not in line:
            continue
        m = re.search(r'time=(\d+):(\d+):(\d+\.\d+)', line)
        if not m:
            continue

        hrs, mins, secs = m.groups()
        played = int(hrs) * 3600 + int(mins) * 60 + float(secs)
        current_seconds = base_seconds + played

        if total_duration_sec > 0:
            write_text_file('time.txt', format_hms(max(0, total_duration_sec - current_seconds)))
        else:
            write_text_file('time.txt', format_hms(current_seconds))

        now = time.time()
        progress[0] = now
        progress[1] = True

        if now - last_save_time > 30:
            update_local_state(ctx["index"], current_seconds, ctx["url"], ctx["title"])
            last_save_time = now
        if now - last_dashboard_time > 30:
            print_dashboard(ctx["title"], ctx["index"], ctx["playlist_len"], current_seconds,
                            has_sub=ctx.get("has_sub", False))
            write_step_summary(ctx["title"], ctx["index"], ctx["playlist_len"], current_seconds,
                               has_sub=ctx.get("has_sub", False))
            last_dashboard_time = now

    process.wait()
    pump_thread.join(timeout=15)

    output_broken = output.failed.is_set() or not output.alive() or pump_thread.is_alive()
    return ReaderResult(process.returncode, current_seconds, stderr_tail, output_broken)


def is_really_finished(result, base_seconds, total_duration_sec):
    """Film GERÇEKTEN bitti mi? Return code 0 tek başına yetmez."""
    if result.returncode != 0:
        return False

    played_seconds = result.stream_seconds - base_seconds

    if played_seconds < 10:
        return False

    if total_duration_sec > 0:
        return result.stream_seconds >= total_duration_sec - END_TOLERANCE_SECONDS

    return played_seconds > 60


# ===================== ANA DÖNGÜ =====================
def start_m3u_stream():
    print(f"🔧 Kullanılan M3U   : {M3U_URL}")
    print(f"🔧 Kullanılan Logo  : {LOGO_URL}")
    print(f"🔧 State dosyası    : {STATE_FILE_NAME}")
    print(f"🔧 RTMP hedefi      : {RTMP_SERVER}")
    print(f"🔧 Decoder thread   : {DECODER_THREADS}")
    print(f"🔧 Altyazı          : {'AÇIK' if SUBTITLE_ENABLED else 'KAPALI'}")

    download_logo()
    current_index, last_seconds, last_url, last_title = get_local_state()

    consecutive_failures = 0
    output = RtmpOutput(RTMP_SERVER)

    try:
        while True:
            playlist = get_m3u_playlist(M3U_URL)
            if not playlist:
                time.sleep(10)
                continue

            if current_index >= len(playlist):
                current_index, last_seconds, last_url, last_title = 0, 0, "", ""

            item = playlist[current_index]
            target_url = item["url"]
            film_title = item["title"]
            sub_url = item.get("sub_url", "")
            playlist_len = len(playlist)

            # 🔥 ALTYAZI: eski dosyaları temizle, yenisini indir
            cleanup_subtitle_files()
            subtitle_file = None
            if SUBTITLE_ENABLED and sub_url:
                print(f"📥 Altyazı indiriliyor: {sub_url}")
                subtitle_file = download_subtitle(sub_url)
                if not subtitle_file:
                    print("ℹ️ Altyazı indirilemedi, altyazısız devam edilecek.")
            elif sub_url:
                print("ℹ️ M3U'da altyazı var ama SUBTITLE_ENABLED=0, atlanıyor.")

            has_sub = subtitle_file is not None

            # Link değiştiyse: aynı film ise geriden devam, farklıysa baştan
            if last_seconds > 0 and last_url and target_url != last_url:
                if last_title and film_title == last_title:
                    old = last_seconds
                    last_seconds = max(0, last_seconds - LINK_CHANGE_REWIND_SECONDS)
                    print(f"🔄 Sıra {current_index + 1}: link değişmiş ama film aynı ('{film_title}'). "
                          f"{old}s yerine {last_seconds}s'den devam edilecek.")
                else:
                    print(f"🆕 Sıra {current_index + 1}: içerik gerçekten değişmiş, baştan başlatılacak.")
                    last_seconds = 0

            last_url = target_url
            last_title = film_title
            write_text_file('title.txt', film_title)

            total_duration_sec = get_total_duration(target_url)
            write_text_file(
                'time.txt',
                format_hms(max(0, total_duration_sec - last_seconds) if total_duration_sec > 0 else 0),
            )

            print("=" * 60)
            print("📺 Maxanimasyon Canlı Aktarım Yayını (1080p 25fps - 2500k) Başlatılıyor")
            print(f"🎬 Oynatılan İçerik  : {film_title}")
            print(f"⏱️ Başlangıç Saniyesi: {last_seconds}")
            print(f"⏱ Toplam Süre      : {format_hms(total_duration_sec) if total_duration_sec > 0 else 'Bilinmiyor'}")
            print(f"💬 Altyazı          : {'Var' if has_sub else 'Yok'}")
            print(f"🚀 Hedef RTMP       : {RTMP_SERVER}")

            print_dashboard(film_title, current_index, playlist_len, last_seconds,
                            status="🟡 Başlatılıyor", has_sub=has_sub)
            write_step_summary(film_title, current_index, playlist_len, last_seconds,
                               status="🟡 Başlatılıyor", has_sub=has_sub)

            if not output.alive():
                output.start()

            _, dual_audio_url = split_dual_url(target_url)
            video_only = bool(
                dual_audio_url
                and DUAL_FALLBACK_AFTER_FAILURES > 0
                and consecutive_failures >= DUAL_FALLBACK_AFTER_FAILURES
            )
            if video_only:
                print(f"🔁 Çift URL art arda {consecutive_failures} kez hata verdi, bu denemede sadece video URL'si kullanılacak.")

            command = build_reader_command(
                target_url, last_seconds, video_only=video_only, subtitle_file=subtitle_file
            )
            print("▶ Okuyucu FFmpeg başlatıldı, kalıcı RTMP'ye aktarılıyor...")

            ctx = {
                "index": current_index,
                "url": target_url,
                "title": film_title,
                "playlist_len": playlist_len,
                "has_sub": has_sub,
            }
            result = run_reader(command, output, last_seconds, total_duration_sec, ctx)
            played = result.stream_seconds - last_seconds

            # ---------- 1) RTMP çıkışı koptu ----------
            if result.output_broken:
                print("🔴 Kalıcı RTMP çıkışı koptu. Çıkış süreci yeniden başlatılacak, film aynı saniyeden devam edecek.")
                if output.stderr_tail:
                    print("🧾 Çıkış FFmpeg son log satırları:")
                    for l in output.stderr_tail:
                        print(f"   {l}")
                output.stop(force=True)
                write_step_summary(film_title, current_index, playlist_len, result.stream_seconds,
                                   status="🔴 RTMP koptu, yeniden bağlanılıyor", has_sub=has_sub)
                last_seconds = result.stream_seconds
                update_local_state(current_index, last_seconds, target_url, film_title)
                retry_delay = 5

            # ---------- 2) Film gerçekten bitti ----------
            elif is_really_finished(result, last_seconds, total_duration_sec):
                print("✅ İçerik bitti, sıradakine geçiliyor (RTMP açık kalıyor).")
                write_step_summary(film_title, current_index, playlist_len, result.stream_seconds,
                                   status="✅ Bitti, sıradakine geçiliyor", has_sub=has_sub)
                current_index += 1
                last_seconds, last_url, last_title = 0, "", ""
                update_local_state(current_index, 0, "", "")
                consecutive_failures = 0
                retry_delay = 0

            # ---------- 3) Kaynak koptu / erken bitti ----------
            else:
                if result.returncode == 0:
                    total_txt = format_hms(total_duration_sec) if total_duration_sec > 0 else "?"
                    print(f"⚠️ Okuyucu erken sonlandı (return code 0 ama film bitmedi): "
                          f"{format_hms(result.stream_seconds)} / {total_txt}")
                elif result.returncode == -6:
                    print("⚠ FFmpeg SIGABRT ile çöktü.")
                elif result.returncode == -9:
                    print("⚠️ Okuyucu FFmpeg watchdog tarafından donma nedeniyle sonlandırıldı.")
                print(f"⚠️ Okuyucu koptu (Return Code: {result.returncode}). Aynı saniyeden tekrar denenecek. (RTMP açık kalıyor)")
                if result.stderr_tail:
                    print("🧾 FFmpeg son log satırları:")
                    for l in result.stderr_tail:
                        print(f"   {l}")
                write_step_summary(film_title, current_index, playlist_len, result.stream_seconds,
                                   status="🔴 Kaynak koptu, tekrar denenecek", has_sub=has_sub)

                if played < FAST_FAIL_THRESHOLD_SECONDS:
                    consecutive_failures += 1
                else:
                    consecutive_failures = 0

                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    print(f"❌ {film_title} akışı sürekli hataya düştü. Sonraki içeriğe geçiliyor...")
                    current_index += 1
                    last_seconds, last_url, last_title = 0, "", ""
                    consecutive_failures = 0
                    update_local_state(current_index, 0, "", "")
                else:
                    last_seconds = result.stream_seconds
                    if consecutive_failures >= SEEK_BACKOFF_AFTER_FAILURES and last_seconds > 0:
                        old = last_seconds
                        last_seconds = max(0, last_seconds - SEEK_BACKOFF_SECONDS)
                        print(f"⏪ Art arda hata: seek konumu {old}s -> {last_seconds}s geri alındı.")
                    last_url = target_url
                    update_local_state(current_index, last_seconds, last_url, film_title)

                retry_delay = (
                    min(5 * (2 ** consecutive_failures), MAX_RETRY_DELAY_SECONDS)
                    if consecutive_failures > 0 else 5
                )

            if retry_delay > 0:
                print(f"⚠️ {retry_delay} saniye sonra tekrar bağlanılıyor...")
                time.sleep(retry_delay)

    except KeyboardInterrupt:
        print("\n🛑 Durduruluyor...")
    finally:
        output.stop()


if __name__ == "__main__":
    start_m3u_stream()
