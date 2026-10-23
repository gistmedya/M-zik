#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import sys
import time
import os
import re
import json
import requests
import threading
from collections import deque
import shutil

# ===================== OTOMATİK FFMPEG KONTROLÜ VE KURULUMU =====================
def ensure_ffmpeg_installed():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("⚙️ Sistemde FFmpeg / FFprobe bulunamadı. Otomatik kurulum başlatılıyor...")
        try:
            cmd = ['apt-get', 'update', '-y']
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            install_cmd = ['apt-get', 'install', '-y', 'ffmpeg']
            subprocess.run(install_cmd, check=True)
            print("✅ FFmpeg başarıyla kuruldu.")
        except Exception as e:
            print(f"⚠️ Otomatik FFmpeg kurulurken hata oluştu veya yetki yetersiz: {e}")
            print("💡 Lütfen terminalden manuel olarak 'sudo apt install ffmpeg' komutunu çalıştırın.")

ensure_ffmpeg_installed()

# ===================== AYARLAR =====================
RTMP_URL = "rtmp://ssh101.bozztv.com:1935/ssh101"
STREAM_KEY = os.getenv("STREAM_KEY") or "zirve"
RTMP_SERVER = f"{RTMP_URL}/{STREAM_KEY}"

M3U8_URL = os.getenv("M3U8_URL") or "https://v2.catcast.tv/content/50736/seg-27708703-v1-a1/index.m3u8"
LOGO_URL = os.getenv("LOGO_URL") or "https://i.hizliresim.com/on1bgkjg.png"

STATE_FILE_NAME = os.getenv("STATE_FILE_NAME", "state_yerli.json")
GITHUB_STEP_SUMMARY = os.getenv("GITHUB_STEP_SUMMARY")

STREAM_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
STREAM_REFERER = "https://vidmody.com/"

LOGO_OPACITY = float(os.getenv("LOGO_OPACITY", "1.0"))
TEXT_OPACITY = float(os.getenv("TEXT_OPACITY", "1.0"))
BOLD_FONT_PATH = os.getenv("BOLD_FONT_PATH", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
REGULAR_FONT_PATH = os.getenv("REGULAR_FONT_PATH", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")

DECODER_THREADS = os.getenv("DECODER_THREADS", "1")

WATCHDOG_STARTUP_SECONDS = int(os.getenv("WATCHDOG_STARTUP_SECONDS", "60"))
WATCHDOG_TIMEOUT_SECONDS = int(os.getenv("WATCHDOG_TIMEOUT_SECONDS", "45"))

END_TOLERANCE_SECONDS = 15
SEEK_BACKOFF_SECONDS = 10
LINK_CHANGE_REWIND_SECONDS = int(os.getenv("LINK_CHANGE_REWIND_SECONDS", "15"))

# Saat gösterge ayarları (sol alt köşe)
CLOCK_TIMEZONE = os.getenv("CLOCK_TIMEZONE", "Europe/Istanbul")

# Hava durumu ayarları
WEATHER_CITIES = ["Adana", "Konya", "İstanbul", "Ankara", "Mersin", "Muğla", "Ağrı", "Diyarbakır"]
WEATHER_ROTATE_SECONDS = 15
WEATHER_UPDATE_SECONDS = int(os.getenv("WEATHER_UPDATE_SECONDS", "600"))
WEATHER_CACHE_DATA = "weather_data.json"

WEATHER_CODE_MAP = {
    "113": ("☀️", "Güneşli"),
    "116": ("⛅", "Parçalı Bulutlu"),
    "119": ("☁️", "Bulutlu"),
    "122": ("☁️", "Kapalı"),
    "143": ("🌫️", "Puslu"),
    "176": ("🌦️", "Hafif Yağmurlu"),
    "179": ("🌨️", "Hafif Kar"),
    "182": ("🌧️", "Karla Karışık Yağmur"),
    "185": ("🌧️", "Karla Karışık Çisenti"),
    "200": ("⛈️", "Gök Gürültülü"),
    "227": ("🌨️", "Kar Fırtınası"),
    "230": ("❄️", "Kar Fırtınası"),
    "248": ("🌫️", "Sisli"),
    "260": ("🌫️", "Donmuş Sis"),
    "263": ("🌦️", "Hafif Çisenti"),
    "266": ("🌦️", "Çisenti"),
    "281": ("🌧️", "Donmuş Çisenti"),
    "284": ("🌧️", "Yoğun Donmuş Çisenti"),
    "293": ("🌦️", "Hafif Yağmur"),
    "296": ("🌧️", "Hafif Yağmur"),
    "299": ("🌧️", "Orta Yağmur"),
    "302": ("🌧️", "Orta Yağmur"),
    "305": ("🌧️", "Şiddetli Yağmur"),
    "308": ("🌧️", "Şiddetli Yağmur"),
    "311": ("🌧️", "Hafif Donmuş Yağmur"),
    "314": ("🌧️", "Donmuş Yağmur"),
    "317": ("🌨️", "Hafif Sulu Kar"),
    "320": ("🌨️", "Sulu Kar"),
    "323": ("🌨️", "Hafif Kar"),
    "326": ("🌨️", "Hafif Kar"),
    "329": ("❄️", "Orta Kar"),
    "332": ("❄️", "Orta Kar"),
    "335": ("❄️", "Yoğun Kar"),
    "338": ("❄️", "Yoğun Kar"),
    "350": ("🌧️", "Buz Kristali"),
    "353": ("🌦️", "Hafif Sağanak"),
    "356": ("🌧️", "Sağanak"),
    "359": ("🌧️", "Şiddetli Sağanak"),
    "362": ("🌨️", "Hafif Sulu Kar Sağanağı"),
    "365": ("🌨️", "Sulu Kar Sağanağı"),
    "368": ("🌨️", "Hafif Kar Sağanağı"),
    "371": ("❄️", "Yoğun Kar Sağanağı"),
    "374": ("🌧️", "Hafif Buz Sağanağı"),
    "377": ("🌧️", "Buz Sağanağı"),
    "386": ("⛈️", "Gök Gürültülü Yağmur"),
    "389": ("⛈️", "Şiddetli Gök Gürültülü Yağmur"),
    "392": ("⛈️", "Gök Gürültülü Kar"),
    "395": ("⛈️", "Yoğun Gök Gürültülü Kar"),
}


def format_hms(total_seconds):
    total_seconds = max(0, int(total_seconds))
    hrs = total_seconds // 3600
    mins = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hrs:02d}:{mins:02d}:{secs:02d}"


# ===================== HAVA DURUMU =====================
def fetch_weather_for_city(city):
    try:
        url = f"https://wttr.in/{city}?format=j1&lang=tr"
        headers = {'User-Agent': 'curl/7.68.0'}
        r = requests.get(url, headers=headers, timeout=12)
        if r.status_code == 200:
            data = r.json()
            current = data.get("current_condition", [{}])[0]
            temp = current.get("temp_C", "--")
            code = str(current.get("weatherCode", ""))
            desc_list = current.get("lang_tr", [{}])
            tr_desc = None
            if desc_list and isinstance(desc_list, list):
                tr_desc = desc_list[0].get("value")
            emoji, tr_label = WEATHER_CODE_MAP.get(code, ("🌡️", tr_desc or "Bilinmiyor"))
            return {
                "city": city,
                "emoji": emoji,
                "label": tr_label,
                "temp": f"{temp}°C",
            }
    except Exception as e:
        print(f"⚠️ Hava durumu alınamadı ({city}): {e}")
    return {
        "city": city,
        "emoji": "🌡️",
        "label": "Bilinmiyor",
        "temp": "--",
    }


def update_weather_file():
    data = {}
    for city in WEATHER_CITIES:
        info = fetch_weather_for_city(city)
        data[city] = info
        time.sleep(0.4)
    try:
        with open(WEATHER_CACHE_DATA, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print("🌤️ Hava durumu güncellendi.")
    except Exception as e:
        print(f"⚠️ Hava durumu dosyası yazılamadı: {e}")


def weather_loop():
    while True:
        update_weather_file()
        time.sleep(WEATHER_UPDATE_SECONDS)


def build_weather_overlay_filters(canvas_h=1080, canvas_w=1920):
    if not os.path.exists(WEATHER_CACHE_DATA):
        try:
            dummy = {c: {"city": c, "emoji": "🌡️", "label": "Bilinmiyor", "temp": "--"} for c in WEATHER_CITIES}
            with open(WEATHER_CACHE_DATA, "w", encoding="utf-8") as f:
                json.dump(dummy, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    try:
        with open(WEATHER_CACHE_DATA, "r", encoding="utf-8") as f:
            wdata = json.load(f)
    except Exception:
        wdata = {}

    filters = []

    box_w = 260
    box_h = 100
    box_x = canvas_w - box_w - 25
    box_y = 25

    for i, city in enumerate(WEATHER_CITIES):
        start = i * WEATHER_ROTATE_SECONDS
        end = (i + 1) * WEATHER_ROTATE_SECONDS
        info = wdata.get(city, {})
        emoji = info.get("emoji", "🌡️")
        temp = info.get("temp", "--")
        label = info.get("label", "Bilinmiyor")

        line1 = f"{emoji}  {city.upper()}"
        line2 = f"{temp}   {label}"

        fname1 = f"weather_{i}_1.txt"
        fname2 = f"weather_{i}_2.txt"
        try:
            with open(fname1, "w", encoding="utf-8") as f:
                f.write(line1)
            with open(fname2, "w", encoding="utf-8") as f:
                f.write(line2)
        except Exception:
            continue

        enable_expr = f"between(t,{start},{end})"

        filters.append(
            f"drawbox=x={box_x+3}:y={box_y+3}:w={box_w}:h={box_h}:"
            f"color=black@0.45:t=fill:enable='{enable_expr}'"
        )
        filters.append(
            f"drawbox=x={box_x}:y={box_y}:w={box_w}:h={box_h}:"
            f"color=0x0F2A44@0.88:t=fill:enable='{enable_expr}'"
        )
        filters.append(
            f"drawbox=x={box_x}:y={box_y}:w={box_w}:h=3:"
            f"color=0x3AA9FF@1.0:t=fill:enable='{enable_expr}'"
        )
        filters.append(
            f"drawbox=x={box_x}:y={box_y}:w=3:h={box_h}:"
            f"color=0x3AA9FF@1.0:t=fill:enable='{enable_expr}'"
        )

        filters.append(
            f"drawtext=textfile='{fname1}':reload=0:"
            f"fontfile='{BOLD_FONT_PATH}':"
            f"fontcolor=white@{TEXT_OPACITY}:fontsize=20:"
            f"x={box_x+15}:y={box_y+14}:"
            f"enable='{enable_expr}'"
        )
        filters.append(
            f"drawtext=textfile='{fname2}':reload=0:"
            f"fontfile='{REGULAR_FONT_PATH}':"
            f"fontcolor=0xCFE8FF@{TEXT_OPACITY}:fontsize=16:"
            f"x={box_x+15}:y={box_y+52}:"
            f"enable='{enable_expr}'"
        )

    return filters


# ===================== KALICI RTMP YAYINCISI =====================
class RtmpPublisher:
    def __init__(self):
        self.proc = None
        self.started_at = 0.0
        self.tail = deque(maxlen=30)

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def ensure_running(self):
        if self.alive():
            return
        self.stop()
        cmd = [
            'ffmpeg', '-hide_banner', '-loglevel', 'warning',
            '-fflags', '+genpts',
            '-analyzeduration', '3000000',
            '-probesize', '3000000',
            '-f', 'mpegts', '-i', 'pipe:0',
            '-c', 'copy',
            '-bsf:a', 'aac_adtstoasc',
            '-flvflags', 'no_duration_filesize',
            '-f', 'flv',
            RTMP_SERVER
        ]
        print(f"📡 Kalıcı RTMP yayıncısı başlatılıyor => {RTMP_SERVER}")
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=0
        )
        self.started_at = time.time()
        threading.Thread(target=self._drain, args=(self.proc,), daemon=True).start()

    def _drain(self, proc):
        try:
            for raw in iter(proc.stderr.readline, b''):
                self.tail.append(raw.decode('utf-8', 'replace').rstrip())
        except Exception:
            pass

    def clock(self):
        return time.time() - self.started_at

    def stop(self):
        if self.proc is None:
            return
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            if self.proc.poll() is None:
                self.proc.kill()
            self.proc.wait(timeout=5)
        except Exception:
            pass
        self.proc = None


publisher = RtmpPublisher()
_duration_cache = {}


def probe_url_of(stream_url):
    return stream_url.split(";")[0].strip() if ";" in stream_url else stream_url


def prefetch_duration(stream_url):
    url = probe_url_of(stream_url)
    duration = get_video_duration_ffprobe(url)
    if duration > 0:
        _duration_cache[url] = duration


def get_video_duration_ffprobe(video_url):
    cmd = [
        'ffprobe',
        '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        '-headers', f"User-Agent: {STREAM_USER_AGENT}\r\nReferer: {STREAM_REFERER}\r\n",
        video_url
    ]
    try:
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=12).decode('utf-8', 'replace').strip()
        duration = float(output.splitlines()[-1].strip())
        if duration > 0:
            print(f"⏱️ ffprobe ile toplam süre tespit edildi: {duration:.1f} saniye ({format_hms(duration)})")
            return duration
    except Exception as e:
        print(f"⚠️ ffprobe ile süre okunamadı: {e}")
    return 0.0


def is_really_finished(current_sec, total_sec):
    if total_sec <= 0:
        return True
    return (total_sec - current_sec) <= END_TOLERANCE_SECONDS


def get_local_state():
    if os.path.exists(STATE_FILE_NAME):
        if os.path.getsize(STATE_FILE_NAME) == 0:
            return 0, 0, "", ""
        try:
            with open(STATE_FILE_NAME, "r", encoding="utf-8") as f:
                data = json.load(f)
                return (
                    data.get("last_index", 0),
                    data.get("last_seconds", 0),
                    data.get("last_url", ""),
                    data.get("last_title", "")
                )
        except Exception as e:
            print(f"⚠️ Yerel state okuma hatası: {e}")
    return 0, 0, "", ""


def update_local_state(index, seconds, url="", title=""):
    tmp_file = f"{STATE_FILE_NAME}.tmp"
    try:
        data = {
            "last_index": int(index),
            "last_seconds": int(seconds),
            "last_url": url,
            "last_title": title,
        }
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, STATE_FILE_NAME)
        print(f"💾 Konum kaydedildi => İndeks: {index}, Saniye: {int(seconds)}")
    except Exception as e:
        print(f"⚠️ Yerel state yazma hatası: {e}")


def download_logo():
    headers = {'User-Agent': STREAM_USER_AGENT}
    try:
        response = requests.get(LOGO_URL, headers=headers, timeout=15)
        if response.status_code == 200 and len(response.content) > 0:
            with open('logo.png', 'wb') as f:
                f.write(response.content)
            print("✅ Logo başarıyla indirildi.")
    except Exception as e:
        print(f"⚠️ Logo indirme hatası: {e}")


def write_title_file(title):
    try:
        with open('title.txt', 'w', encoding='utf-8') as f:
            f.write(title)
    except Exception as e:
        print(f"⚠️ Başlık dosyası yazma hatası: {e}")


def print_dashboard(title, index, playlist_len, seconds, status="🟢 Yayında"):
    print("┌" + "─" * 58 + "┐")
    print(f"│ 🎬 İçerik         : {title[:36]:<36} │")
    print(f"│ 🔢 Sıra           : {index + 1}/{playlist_len:<32} │")
    print(f"│ ⏱️  Geçen Süre     : {format_hms(seconds):<36} │")
    print(f"│ 📡 Durum          : {status:<36} │")
    print("└" + "─" * 58 + "┘")


def write_step_summary(title, index, playlist_len, seconds, status="🟢 Yayında"):
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
            f"| 🕒 Son güncelleme | {time.strftime('%Y-%m-%d %H:%M:%S')} |\n"
        )
        with open(GITHUB_STEP_SUMMARY, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        print(f"⚠️ Step summary yazma hatası: {e}")


def start_stream():
    print(f"🔧 Kullanılan M3U8  : {M3U8_URL}")
    print(f"🔧 Kullanılan Logo  : {LOGO_URL}")
    print(f"🔧 State dosyası    : {STATE_FILE_NAME}")
    print(f"🔧 RTMP hedefi      : {RTMP_SERVER}")

    download_logo()

    threading.Thread(target=weather_loop, daemon=True).start()
    update_weather_file()

    current_index, last_seconds, last_url, last_title = get_local_state()

    consecutive_fast_failures = 0
    FAST_FAIL_THRESHOLD_SECONDS = 20
    MAX_RETRY_DELAY_SECONDS = 120

    playlist = [{"url": M3U8_URL, "title": "Canlı Yayın"}]
    if current_index != 0:
        current_index = 0
        last_seconds = 0
        last_url = ""
        last_title = ""
        update_local_state(0, 0, "", "")

    while True:
        playlist = [{"url": M3U8_URL, "title": "Canlı Yayın"}]
        if current_index >= len(playlist):
            current_index = 0
            last_seconds = 0
            last_url = ""
            last_title = ""

        current_item = playlist[current_index]
        target_stream_url = current_item["url"]
        film_title = current_item["title"]

        if last_seconds > 0 and last_url and target_stream_url != last_url:
            if last_title and film_title == last_title:
                old_seconds = last_seconds
                last_seconds = max(0, last_seconds - LINK_CHANGE_REWIND_SECONDS)
                print(f"🔄 İçerik linki yenilenmiş. {old_seconds}s yerine {last_seconds}s'den başlanacak.")
            else:
                last_seconds = 0

        last_url = target_stream_url
        last_title = film_title
        write_title_file(film_title)

        probe_url = probe_url_of(target_stream_url)
        total_duration_sec = _duration_cache.pop(probe_url, 0.0)
        if total_duration_sec <= 0:
            total_duration_sec = get_video_duration_ffprobe(probe_url)

        print("=" * 60)
        print("📺 Maxanimasyon Canlı Aktarım Yayını Başlatılıyor")
        print(f"🎬 Oynatılan İçerik  : {film_title}")
        print(f"⏱️ Başlangıç Saniyesi: {last_seconds}")
        print(f"⏱️ Toplam Süre      : {format_hms(total_duration_sec) if total_duration_sec > 0 else 'Bilinmiyor'}")

        headers_arg = f"User-Agent: {STREAM_USER_AGENT}\r\nReferer: https://vidmody.com/\r\nOrigin: https://vidmody.com\r\n"

        input_options = [
            '-re',
            '-headers', headers_arg,
            '-protocol_whitelist', 'file,http,https,tcp,tls,crypto',
            '-err_detect', 'ignore_err',
            '-fflags', '+genpts+discardcorrupt',
            '-thread_queue_size', '1024',
            '-max_interleave_delta', '0',
            '-analyzeduration', '10000000',
            '-probesize', '10000000',
            '-reconnect', '1',
            '-reconnect_at_eof', '1',
            '-reconnect_streamed', '1',
            '-reconnect_delay_max', '10',
            '-rw_timeout', '10000000',
            '-threads', DECODER_THREADS,
        ]

        seek_args = ['-ss', str(last_seconds)] if last_seconds > 0 else []

        if ";" in target_stream_url:
            v_url, a_url = target_stream_url.split(";", 1)
            input_args = input_options + seek_args + ['-i', v_url.strip()] + input_options + seek_args + ['-i', a_url.strip()]
            audio_map = ['-map', '1:a:0?']
            logo1_input_index = 2
        else:
            input_args = input_options + seek_args + ['-i', target_stream_url]
            audio_map = ['-map', '0:a:0?']
            logo1_input_index = 1

        print_dashboard(film_title, current_index, len(playlist), last_seconds, status="🟡 Başlatılıyor")

        has_logo1 = os.path.exists('logo.png') and os.path.getsize('logo.png') > 0
        title_drawtext = f"drawtext=textfile='title.txt':reload=1:fontfile='{BOLD_FONT_PATH}':fontcolor=white@{TEXT_OPACITY}:fontsize=19:x=w-tw-20:y=h-th-20"

        # Sol altta 24 saat formatında canlı sistem saati (%H: 24 saat tabanlıdır)
        # Okunaklılık için yarı saydam siyah kutu eklendi
        time_text = "%{localtime\\:%H\\:%M\\:%S}"
        time_drawtext = (
            f"drawtext=text='{time_text}':fontfile='{BOLD_FONT_PATH}':"
            f"fontcolor=white@{TEXT_OPACITY}:fontsize=22:x=20:y=h-th-28:"
            f"box=1:boxcolor=black@0.55:boxborderw=8"
        )

        weather_filters = build_weather_overlay_filters(canvas_w=1920, canvas_h=1080)
        weather_chain = ",".join(weather_filters)

        if has_logo1:
            logo_inputs = ['-i', 'logo.png']
            filter_str = (
                '[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=25[main];'
                f'[{logo1_input_index}:v]scale=-2:85,format=rgba,colorchannelmixer=aa={LOGO_OPACITY}[logo1];'
                '[main][logo1]overlay=50:50[tmp1];'
                f'[tmp1]{weather_chain}[tmp1b];'
                f'[tmp1b]{title_drawtext}[tmp2];'
                f'[tmp2]{time_drawtext}[v]'
            )
        else:
            logo_inputs = []
            filter_str = (
                '[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=25[main];'
                f'[main]{weather_chain}[tmp1b];'
                f'[tmp1b]{title_drawtext}[tmp2];'
                f'[tmp2]{time_drawtext}[v]'
            )

        publisher.ensure_running()
        ts_offset = publisher.clock() + 1.0

        command = ['ffmpeg'] + input_args + logo_inputs + [
            '-filter_complex', filter_str,
            '-map', '[v]'
        ] + audio_map + [
            '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
            '-r', '25', '-b:v', '2500k', '-maxrate', '2500k', '-bufsize', '3000k',
            '-g', '50', '-c:a', 'aac', '-b:a', '128k', '-ac', '2', '-ar', '44100',
            '-output_ts_offset', f'{ts_offset:.3f}', '-flush_packets', '1',
            '-f', 'mpegts', 'pipe:1'
        ]

        # TZ değişkeni sayesinde saat Türkiye saatiyle (varsayılan) gösterilir
        process = subprocess.Popen(
            command,
            stdout=publisher.proc.stdin,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            env={**os.environ, "TZ": CLOCK_TIMEZONE}
        )

        if len(playlist) > 1:
            next_item = playlist[(current_index + 1) % len(playlist)]
            threading.Thread(target=prefetch_duration, args=(next_item["url"],), daemon=True).start()

        last_save_time = time.time()
        last_dashboard_time = time.time()
        current_stream_seconds = last_seconds
        stderr_tail = deque(maxlen=40)

        last_progress_time = [time.time()]
        has_started_progress = [False]

        def _watchdog(proc=process, progress_ref=last_progress_time, started_ref=has_started_progress):
            while proc.poll() is None:
                time.sleep(5)
                timeout = WATCHDOG_TIMEOUT_SECONDS if started_ref[0] else WATCHDOG_STARTUP_SECONDS
                if time.time() - progress_ref[0] > timeout:
                    print(f"🚨 Watchdog: {timeout} saniyedir veri akışı yok! Süreç kilitlendi, sonlandırılıyor.")
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    break

        threading.Thread(target=_watchdog, daemon=True).start()

        while True:
            line = process.stderr.readline()
            if not line and process.poll() is not None:
                break

            if line:
                stderr_tail.append(line.rstrip())

            if "time=" in line:
                time_match = re.search(r'time=(\d+):(\d+):(\d+\.\d+)', line)
                if time_match:
                    hrs, mins, secs = time_match.groups()
                    played_seconds = int(hrs) * 3600 + int(mins) * 60 + float(secs)
                    current_stream_seconds = last_seconds + played_seconds

                    now = time.time()
                    last_progress_time[0] = now
                    has_started_progress[0] = True

                    if now - last_save_time > 30:
                        update_local_state(current_index, current_stream_seconds, target_stream_url, film_title)
                        last_save_time = now

                    if now - last_dashboard_time > 30:
                        print_dashboard(film_title, current_index, len(playlist), current_stream_seconds)
                        write_step_summary(film_title, current_index, len(playlist), current_stream_seconds)
                        last_dashboard_time = now

        finished_naturally = (process.returncode == 0) and is_really_finished(current_stream_seconds, total_duration_sec)

        if finished_naturally:
            print("✅ İçerik gerçekten bitti, başa dönülüyor.")
            write_step_summary(film_title, current_index, len(playlist), current_stream_seconds, status="✅ Bitti, başa dönülüyor")
            current_index = 0
            last_seconds = 0
            last_url = ""
            last_title = ""
            update_local_state(current_index, 0, "", "")
            consecutive_fast_failures = 0
        else:
            if process.returncode == 0:
                print(f"⚠️ FFmpeg 0 kodu verdi ancak film tamamlanmadı ({format_hms(current_stream_seconds)} / {format_hms(total_duration_sec)}). Erken kopma sayıldı!")
            elif process.returncode == -9:
                print("⚠️ FFmpeg watchdog (donma) nedeniyle sonlandırıldı.")

            write_step_summary(film_title, current_index, len(playlist), current_stream_seconds, status="🔴 Bağlantı koptu, tekrar denenecek")

            duration_this_attempt = current_stream_seconds - last_seconds
            if duration_this_attempt < FAST_FAIL_THRESHOLD_SECONDS:
                consecutive_fast_failures += 1
            else:
                consecutive_fast_failures = 0

            if consecutive_fast_failures >= 3:
                print(f"❌ {film_title} akışı sürekli hataya düştü. Baştan başlanıyor...")
                current_index = 0
                last_seconds = 0
                last_url = ""
                last_title = ""
                consecutive_fast_failures = 0
                update_local_state(current_index, 0, "", "")
            else:
                if consecutive_fast_failures > 1 and current_stream_seconds > SEEK_BACKOFF_SECONDS:
                    last_seconds = max(0, current_stream_seconds - SEEK_BACKOFF_SECONDS)
                    print(f"⏪ Takılma koruması: {SEEK_BACKOFF_SECONDS} saniye geriye sarılarak ({last_seconds}s) tekrar denenecek.")
                else:
                    last_seconds = current_stream_seconds

                last_url = target_stream_url
                update_local_state(current_index, last_seconds, last_url, film_title)

        retry_delay = 0 if finished_naturally else (min(5 * (2 ** consecutive_fast_failures), MAX_RETRY_DELAY_SECONDS) if consecutive_fast_failures > 0 else 5)
        if retry_delay > 0:
            print(f"⚠️ {retry_delay} saniye sonra tekrar bağlanılıyor...")
            time.sleep(retry_delay)


if __name__ == "__main__":
    try:
        start_stream()
    except KeyboardInterrupt:
        print("\n🛑 Durduruluyor...")
    finally:
        publisher.stop()
