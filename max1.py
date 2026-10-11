import os
import re
import json
import time
import shutil
import threading
import subprocess
import requests

from datetime import datetime, timezone


# =========================================================
# 1. RTMP AYARLARI
# =========================================================

RTMP_URL = "rtmp://ssh101.bozztv.com:1935/ssh101"
STREAM_KEY = os.getenv("STREAM_KEY") or "megatv_@"

STREAM_URL = f"{RTMP_URL.rstrip('/')}/{STREAM_KEY}"


# =========================================================
# 2. M3U VE LOGO AYARLARI
# =========================================================

M3U_URL = (
    "https://raw.githubusercontent.com/"
    "ino8090/0101/refs/heads/main/deneme.m3u"
)

# Buraya logonun tam adresini yaz.
LOGO_URL = "https://raw.githubusercontent.com/ino8090/0101/refs/heads/main/file_00000000d06c8210b512e76e052d143e.png"


# =========================================================
# 3. DOSYA AYARLARI
# =========================================================

STATE_FILE = "m.json"
WORK_DIR = "subtitle_work"

LOGO_FILE = os.path.join(WORK_DIR, "logo.png")
SUBTITLE_FILE = os.path.abspath(
    os.path.join(WORK_DIR, "current_subtitle.srt")
)

os.makedirs(WORK_DIR, exist_ok=True)


# =========================================================
# 4. VİDEO AYARLARI
# =========================================================

VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
FPS = 25

VIDEO_PRESET = "veryfast"
VIDEO_BITRATE = "4500k"
AUDIO_BITRATE = "128k"

LOGO_WIDTH = 250
LOGO_X = 50
LOGO_Y = 50


# =========================================================
# 5. ALTYAZI AYARLARI
# =========================================================

FONT_NAME = "DejaVu Sans"
FONT_SIZE = 24

FONT_COLOR = "&H00FFFFFF"
OUTLINE_COLOR = "&H00000000"
BACKGROUND_COLOR = "&H80000000"

OUTLINE_SIZE = 2
SHADOW_SIZE = 1
SUBTITLE_MARGIN = 35


# =========================================================
# 6. GENEL AYARLAR
# =========================================================

REQUEST_TIMEOUT = 30
STATE_SAVE_INTERVAL = 5
RETRY_DELAY = 10

USER_AGENT = "Mozilla/5.0"

state_lock = threading.Lock()
stop_requested = threading.Event()


# =========================================================
# 7. LOG
# =========================================================

def log(message):
    print(
        datetime.now().strftime("[%Y-%m-%d %H:%M:%S]"),
        message,
        flush=True
    )


# =========================================================
# 8. FFmpeg KONTROLÜ
# =========================================================

def check_ffmpeg():
    if not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg bulunamadı.")

    if not shutil.which("ffprobe"):
        raise RuntimeError("FFprobe bulunamadı.")

    log("FFmpeg ve FFprobe hazır.")


# =========================================================
# 9. JSON DURUM DOSYASI
# =========================================================

def default_state():
    return {
        "version": 1,
        "current_index": 0,
        "current_url": None,
        "current_title": None,
        "position_seconds": 0.0,
        "duration_seconds": 0.0,
        "status": "idle",
        "updated_at": None
    }


def load_state():
    if not os.path.isfile(STATE_FILE):
        log("Durum dosyası bulunamadı. Yeni durum oluşturulacak.")
        return default_state()

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        state = default_state()
        state.update(data)

        state["current_index"] = max(
            0, int(state.get("current_index", 0))
        )

        state["position_seconds"] = max(
            0.0, float(state.get("position_seconds", 0.0))
        )

        state["duration_seconds"] = max(
            0.0, float(state.get("duration_seconds", 0.0))
        )

        log(
            "JSON durum dosyası yüklendi. "
            f"Son konum: {state['position_seconds']:.1f} saniye"
        )

        return state

    except Exception as error:
        log(f"JSON okunamadı: {error}")
        log("Yeni durum dosyası oluşturulacak.")
        return default_state()


def save_state(state):
    with state_lock:
        state["updated_at"] = datetime.now(
            timezone.utc
        ).isoformat()

        temp_file = STATE_FILE + ".tmp"

        try:
            with open(temp_file, "w", encoding="utf-8") as file:
                json.dump(
                    state,
                    file,
                    ensure_ascii=False,
                    indent=2
                )
                file.flush()
                os.fsync(file.fileno())

            os.replace(temp_file, STATE_FILE)

        except Exception as error:
            log(f"JSON kaydetme hatası: {error}")

            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except OSError:
                pass


# =========================================================
# 10. M3U LİSTESİNİ OKU
# =========================================================

def get_m3u_playlist():
    response = requests.get(
        M3U_URL,
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": USER_AGENT}
    )

    response.raise_for_status()

    playlist = []

    pending_title = None
    pending_subtitle = None

    for raw_line in response.text.splitlines():
        line = raw_line.strip()

        if not line:
            continue

        if line.startswith("#EXTINF:"):
            if "," in line:
                pending_title = line.split(",", 1)[1].strip()
            else:
                pending_title = "İsimsiz"

        elif line.startswith("#EXTVLCOPT:sub-url="):
            pending_subtitle = line.split("=", 1)[1].strip()

        elif line.startswith("#"):
            continue

        elif line.startswith(("http://", "https://")):
            playlist.append({
                "title": pending_title or "İsimsiz",
                "url": line,
                "subtitle_url": pending_subtitle
            })

            pending_title = None
            pending_subtitle = None

    return playlist


# =========================================================
# 11. LOGO İNDİRME
# =========================================================

def download_logo():
    if (
        not LOGO_URL
        or LOGO_URL == "LOGO_URL_BURAYA"
        or not LOGO_URL.startswith(("http://", "https://"))
    ):
        log("Logo adresi ayarlanmamış. Logosuz devam edilecek.")
        return None

    try:
        response = requests.get(
            LOGO_URL,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT}
        )

        response.raise_for_status()

        if not response.content:
            raise ValueError("Logo dosyası boş.")

        with open(LOGO_FILE, "wb") as file:
            file.write(response.content)

        check = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_type",
                "-of", "default=noprint_wrappers=1:nokey=1",
                LOGO_FILE
            ],
            capture_output=True,
            text=True,
            timeout=15
        )

        if check.returncode != 0 or "video" not in check.stdout:
            raise ValueError("İndirilen dosya geçerli bir görsel değil.")

        log("Logo indirildi.")
        return LOGO_FILE

    except Exception as error:
        log(f"Logo indirilemedi: {error}")
        return None


# =========================================================
# 12. ALTYAZI İNDİRME
# =========================================================

def download_subtitle(subtitle_url):
    if os.path.exists(SUBTITLE_FILE):
        os.remove(SUBTITLE_FILE)

    if not subtitle_url:
        log("Bu film için harici altyazı belirtilmemiş.")
        return None

    try:
        response = requests.get(
            subtitle_url,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT}
        )

        response.raise_for_status()

        if not response.content:
            raise ValueError("Altyazı dosyası boş.")

        decoded = None

        for encoding in ("utf-8-sig", "utf-8", "cp1254", "latin-1"):
            try:
                decoded = response.content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue

        if decoded is None:
            raise ValueError("Altyazı okunamadı.")

        decoded = decoded.replace("\r\n", "\n").replace("\r", "\n")
        decoded = decoded.lstrip("\ufeff")

        # WebVTT başlığını kaldır.
        decoded = re.sub(
            r"^\s*WEBVTT[^\n]*\n",
            "",
            decoded,
            count=1
        )

        # WebVTT zaman satırlarının ek ayarlarını kaldır.
        decoded = re.sub(
            r"(-->\s*\d{2}:\d{2}:\d{2}[.,]\d{3})[^\n]*",
            r"\1",
            decoded
        )

        if "<html" in decoded[:1000].lower():
            raise ValueError("Altyazı yerine HTML sayfası indirildi.")

        if not re.search(
            r"\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->",
            decoded
        ):
            raise ValueError("Geçerli altyazı zaman damgası bulunamadı.")

        with open(
            SUBTITLE_FILE,
            "w",
            encoding="utf-8-sig"
        ) as file:
            file.write(decoded)

        log("Altyazı hazır.")
        return SUBTITLE_FILE

    except Exception as error:
        log(f"Altyazı indirilemedi: {error}")

        if os.path.exists(SUBTITLE_FILE):
            os.remove(SUBTITLE_FILE)

        return None


# =========================================================
# 13. VİDEO SÜRESİNİ BUL
# =========================================================

def get_video_duration(video_url):
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_url
            ],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            return 0.0

        value = result.stdout.strip()

        if not value or value.upper() == "N/A":
            return 0.0

        duration = float(value)

        if duration < 0:
            return 0.0

        return duration

    except Exception as error:
        log(f"Video süresi okunamadı: {error}")
        return 0.0


# =========================================================
# 14. FFmpeg FİLTRE YOLU
# =========================================================

def escape_filter_path(path):
    path = os.path.abspath(path)

    path = path.replace("\\", "\\\\")
    path = path.replace(":", "\\:")
    path = path.replace("'", "\\'")
    path = path.replace(",", "\\,")

    return path


# =========================================================
# 15. FFmpeg KOMUTUNU OLUŞTUR
# =========================================================

def build_ffmpeg_command(item, seek_seconds, logo_path, subtitle_path):
    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-loglevel", "warning",
        "-nostats",
        "-progress", "pipe:1",
        "-re"
    ]

    # Başlangıç zamanını girişten önce uygula.
    if seek_seconds > 0:
        command.extend([
            "-ss", str(max(0, seek_seconds))
        ])

    command.extend([
        "-i", item["url"]
    ])

    has_logo = bool(
        logo_path and os.path.isfile(logo_path)
    )

    if has_logo:
        command.extend([
            "-loop", "1",
            "-framerate", str(FPS),
            "-i", logo_path
        ])

    filters = [
        (
            f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
            "force_original_aspect_ratio=decrease"
        ),
        (
            f"pad={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
            "(ow-iw)/2:(oh-ih)/2:black"
        ),
        f"fps={FPS}"
    ]

    if subtitle_path and os.path.isfile(subtitle_path):
        subtitle_path_escaped = escape_filter_path(subtitle_path)

        force_style = (
            f"FontName={FONT_NAME},"
            f"FontSize={FONT_SIZE},"
            f"PrimaryColour={FONT_COLOR},"
            f"OutlineColour={OUTLINE_COLOR},"
            f"BackColour={BACKGROUND_COLOR},"
            "BorderStyle=1,"
            f"Outline={OUTLINE_SIZE},"
            f"Shadow={SHADOW_SIZE},"
            "Alignment=2,"
            f"MarginV={SUBTITLE_MARGIN}"
        )

        filters.append(
            f"subtitles='{subtitle_path_escaped}':"
            f"force_style='{force_style}'"
        )

    filter_complex = f"[0:v]{','.join(filters)}[base]"

    if has_logo:
        filter_complex += (
            f";[1:v]scale={LOGO_WIDTH}:-1[logo]"
            f";[base][logo]overlay={LOGO_X}:{LOGO_Y}[outv]"
        )

        video_map = "[outv]"

    else:
        video_map = "[base]"

    command.extend([
        "-filter_complex", filter_complex,
        "-map", video_map,
        "-map", "0:a:0?",
        "-c:v", "libx264",
        "-preset", VIDEO_PRESET,
        "-b:v", VIDEO_BITRATE,
        "-pix_fmt", "yuv420p",
        "-r", str(FPS),
        "-c:a", "aac",
        "-b:a", AUDIO_BITRATE,
        "-ar", "44100",
        "-f", "flv",
        STREAM_URL
    ])

    return command


# =========================================================
# 16. BİR FİLMİ KALDIĞI YERDEN YAYINLA
# =========================================================

def publish_video(item, state, logo_path):
    duration = get_video_duration(item["url"])

    seek_seconds = max(
        0.0,
        float(state.get("position_seconds", 0.0))
    )

    # Film bitmeye çok yakınsa baştan oynatmak yerine
    # ana döngünün bir sonraki filme geçmesini sağla.
    if duration > 0 and seek_seconds >= duration - 5:
        log("Kaydedilmiş film bitmiş. Sonraki filme geçilecek.")
        return "finished"

    subtitle_path = download_subtitle(item.get("subtitle_url"))

    state["current_url"] = item["url"]
    state["current_title"] = item["title"]
    state["duration_seconds"] = duration
    state["status"] = "starting"

    save_state(state)

    log(f"Film: {item['title']}")
    log(f"Devam konumu: {seek_seconds:.1f} saniye")

    command = build_ffmpeg_command(
        item,
        seek_seconds,
        logo_path,
        subtitle_path
    )

    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )

    except Exception as error:
        log(f"FFmpeg başlatılamadı: {error}")
        state["status"] = "error"
        save_state(state)
        return "retry"

    stderr_lines = []

    def read_stderr():
        try:
            for line in process.stderr:
                line = line.rstrip()

                if line:
                    stderr_lines.append(line)
                    log("[FFmpeg] " + line)

                    # Belleğin sınırsız büyümesini önle.
                    if len(stderr_lines) > 100:
                        del stderr_lines[:50]

        except Exception:
            pass

    stderr_thread = threading.Thread(
        target=read_stderr,
        daemon=True
    )

    stderr_thread.start()

    last_save_time = time.monotonic()
    last_position = seek_seconds
    progress_position = seek_seconds

    state["status"] = "running"
    save_state(state)

    try:
        for line in process.stdout:
            if stop_requested.is_set():
                break

            line = line.strip()

            if line.startswith("out_time="):
                timestamp = line.split("=", 1)[1].strip()

                match = re.match(
                    r"(\d+):(\d+):(\d+)(?:\.(\d+))?",
                    timestamp
                )

                if match:
                    hours = int(match.group(1))
                    minutes = int(match.group(2))
                    seconds = int(match.group(3))

                    fraction = match.group(4) or "0"

                    if fraction:
                        fraction = float("0." + fraction)
                    else:
                        fraction = 0.0

                    elapsed = (
                        hours * 3600
                        + minutes * 60
                        + seconds
                        + fraction
                    )

                    # -ss girişten önce uygulandığı için,
                    # FFmpeg'in gösterdiği süreye seek eklenir.
                    absolute_position = seek_seconds + elapsed

                    if absolute_position >= last_position:
                        progress_position = absolute_position

            if time.monotonic() - last_save_time >= STATE_SAVE_INTERVAL:
                state["position_seconds"] = progress_position
                state["status"] = "running"

                save_state(state)

                last_position = progress_position
                last_save_time = time.monotonic()

        if stop_requested.is_set():
            try:
                process.terminate()
                process.wait(timeout=10)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

            state["position_seconds"] = progress_position
            state["status"] = "stopped"
            save_state(state)

            return "stopped"

        return_code = process.wait()

        stderr_thread.join(timeout=2)

        state["position_seconds"] = progress_position

        if return_code == 0:
            state["position_seconds"] = 0.0
            state["status"] = "finished"

            save_state(state)

            log("Film tamamlandı.")
            return "finished"

        state["status"] = "retry"
        save_state(state)

        log(
            f"FFmpeg {return_code} koduyla kapandı. "
            f"Kaydedilen konum: {progress_position:.1f} saniye"
        )

        return "retry"

    except KeyboardInterrupt:
        stop_requested.set()

        try:
            process.terminate()
            process.wait(timeout=10)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

        state["position_seconds"] = progress_position
        state["status"] = "stopped"
        save_state(state)

        return "stopped"

    finally:
        if process.stdout:
            process.stdout.close()

        if process.stderr:
            process.stderr.close()

        if os.path.exists(SUBTITLE_FILE):
            try:
                os.remove(SUBTITLE_FILE)
            except OSError:
                pass


# =========================================================
# 17. ANA PROGRAM
# =========================================================

def main():
    check_ffmpeg()

    state = load_state()
    logo_path = download_logo()

    while not stop_requested.is_set():
        try:
            playlist = get_m3u_playlist()

            if not playlist:
                log("M3U listesinde film bulunamadı.")
                time.sleep(RETRY_DELAY)
                continue

            # Önceki kaydedilen filmi URL ile bul.
            saved_url = state.get("current_url")
            saved_index = None

            if saved_url:
                for index, item in enumerate(playlist):
                    if item["url"] == saved_url:
                        saved_index = index
                        break

            if saved_index is not None:
                current_index = saved_index

            else:
                current_index = min(
                    int(state.get("current_index", 0)),
                    len(playlist) - 1
                )

                if saved_url:
                    log(
                        "Kaydedilmiş film listede bulunamadı. "
                        "Kaydedilen liste konumundan devam edilecek."
                    )

                    state["position_seconds"] = 0.0

            while current_index < len(playlist):
                if stop_requested.is_set():
                    break

                item = playlist[current_index]

                state["current_index"] = current_index
                state["current_url"] = item["url"]
                state["current_title"] = item["title"]

                save_state(state)

                result = publish_video(
                    item,
                    state,
                    logo_path
                )

                if result == "stopped":
                    return

                if result == "finished":
                    current_index += 1

                    state["current_index"] = current_index
                    state["position_seconds"] = 0.0

                    if current_index < len(playlist):
                        state["current_url"] = playlist[current_index]["url"]
                        state["current_title"] = playlist[current_index]["title"]
                    else:
                        state["current_url"] = None
                        state["current_title"] = None

                    save_state(state)
                    time.sleep(2)

                elif result == "retry":
                    log(
                        f"{RETRY_DELAY} saniye sonra "
                        "aynı film yeniden denenecek."
                    )
                    time.sleep(RETRY_DELAY)

                    # Aynı film ve kaydedilmiş konum korunur.

            if stop_requested.is_set():
                break

            # Listenin sonuna gelindiğinde yeni tur başlat.
            state["current_index"] = 0
            state["current_url"] = None
            state["current_title"] = None
            state["position_seconds"] = 0.0
            state["status"] = "idle"

            save_state(state)

            log("M3U listesinin sonuna ulaşıldı. Yeni tur başlıyor.")
            time.sleep(2)

        except KeyboardInterrupt:
            stop_requested.set()
            state["status"] = "stopped"
            save_state(state)
            break

        except Exception as error:
            log(f"Ana program hatası: {error}")
            state["status"] = "error"
            save_state(state)
            time.sleep(RETRY_DELAY)

    log("Program durduruldu.")


if __name__ == "__main__":
    main()
