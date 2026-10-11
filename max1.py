import os
import re
import json
import time
import urllib.request
import subprocess
from datetime import datetime
from urllib.parse import urlparse

# =========================================================
# AYARLAR
# =========================================================

M3U_URL = (
    "https://raw.githubusercontent.com/ino8090/0101/"
    "refs/heads/main/deneme.m3u"
)

LOGO_URL = (
    "https://raw.githubusercontent.com/ino8090/0101/"
    "refs/heads/main/1787671958979.png"
)

RTMP_URL = "rtmp://ssh101.bozztv.com:1935/ssh101"
STREAM_KEY = os.getenv("STREAM_KEY") or "megatv_@"
STREAM_OUTPUT = f"{RTMP_URL.rstrip('/')}/{STREAM_KEY}"

STATE_FILE = "m.json"
LOGO_FILE = "maxpremier_logo.png"

RETRY_SECONDS = 10
LOGO_WIDTH = 160

# =========================================================
# LOG
# =========================================================

def log(message):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}", flush=True)


# =========================================================
# DURUM DOSYASI
# =========================================================

def load_state():
    if not os.path.exists(STATE_FILE):
        log("Durum dosyası bulunamadı. Yeni durum oluşturulacak.")

        return {
            "index": 0,
            "title": "",
            "url": "",
            "position": 0.0,
            "status": "starting"
        }

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            state = json.load(file)

        log("Önceki yayın durumu yüklendi.")

        return {
            "index": int(state.get("index", 0)),
            "title": state.get("title", ""),
            "url": state.get("url", ""),
            "position": float(state.get("position", 0)),
            "status": state.get("status", "starting")
        }

    except Exception as error:
        log(f"Durum dosyası okunamadı: {error}")

        return {
            "index": 0,
            "title": "",
            "url": "",
            "position": 0.0,
            "status": "starting"
        }


def save_state(state):
    temporary_file = STATE_FILE + ".tmp"

    try:
        with open(temporary_file, "w", encoding="utf-8") as file:
            json.dump(
                state,
                file,
                ensure_ascii=False,
                indent=2
            )

        os.replace(temporary_file, STATE_FILE)

    except Exception as error:
        log(f"Durum kaydedilemedi: {error}")


# =========================================================
# İNTERNETTEN DOSYA İNDİRME
# =========================================================

def download_file(url, destination):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read()

    with open(destination, "wb") as file:
        file.write(data)


# =========================================================
# URL TEMİZLEME
# =========================================================

def normalize_stream_line(line):
    """
    Örnek hatalı satır:
    [https://site/video.m3u8;https://site/audio.m3u8.]
    (https://site/audio.m3u8.)

    Köşeli parantez ve Markdown bağlantısı varsa,
    öncelikle görünen metin alınır.
    """

    line = line.strip()

    if line.startswith("[") and "](" in line:
        line = line[1:line.index("](")]

    line = line.strip()

    # Dıştaki parantezleri temizle.
    line = line.strip("[]() \t")

    # Noktalı virgül video ve ses adreslerini ayırabilir.
    parts = []

    for part in line.split(";"):
        part = part.strip()

        # Adresin sonuna yanlışlıkla eklenen noktalama işaretlerini temizle.
        part = part.rstrip(".,;)]} \t")

        if part.startswith("http://") or part.startswith("https://"):
            parts.append(part)

    return parts


def clean_subtitle_url(url):
    if not url:
        return ""

    url = url.strip()

    if url.startswith("[") and "](" in url:
        url = url[1:url.index("](")]

    url = url.strip().strip("[]() \t")
    url = url.rstrip(".,;)]} \t")

    if url.startswith(("http://", "https://")):
        return url

    return ""


# =========================================================
# M3U LİSTESİNİ OKUMA
# =========================================================

def get_m3u_playlist():
    log("M3U listesi indiriliyor...")

    request = urllib.request.Request(
        M3U_URL,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        content = response.read().decode(
            "utf-8-sig",
            errors="replace"
        )

    lines = content.splitlines()

    playlist = []

    current_title = "Bilinmeyen film"
    current_subtitle = ""

    for raw_line in lines:
        line = raw_line.strip()

        if not line:
            continue

        if line.startswith("#EXTINF"):
            if "," in line:
                current_title = line.rsplit(",", 1)[-1].strip()
            else:
                current_title = "Bilinmeyen film"

            current_subtitle = ""
            continue

        if line.startswith("#EXTVLCOPT:sub-url="):
            current_subtitle = clean_subtitle_url(
                line.split("=", 1)[1]
            )
            continue

        if line.startswith("#"):
            continue

        urls = normalize_stream_line(line)

        if not urls:
            continue

        video_url = urls[0]

        # İkinci adres varsa ayrı ses kaynağı olarak kullan.
        audio_url = urls[1] if len(urls) >= 2 else ""

        playlist.append({
            "title": current_title,
            "url": video_url,
            "audio_url": audio_url,
            "subtitle_url": current_subtitle
        })

        log(f"M3U kaydı: {current_title}")

        if audio_url:
            log("Ayrı ses adresi bulundu.")

        current_title = "Bilinmeyen film"
        current_subtitle = ""

    if not playlist:
        raise RuntimeError(
            "M3U listesinde geçerli yayın adresi bulunamadı."
        )

    log(f"Toplam {len(playlist)} kayıt bulundu.")

    return playlist


# =========================================================
# FFMPEG KONTROLÜ
# =========================================================

def check_ffmpeg():
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True
        )

        subprocess.run(
            ["ffprobe", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True
        )

        log("FFmpeg ve FFprobe hazır.")

    except Exception:
        raise RuntimeError(
            "FFmpeg veya FFprobe bulunamadı. "
            "Çalıştırma ortamına FFmpeg kurulmalı."
        )


# =========================================================
# LOGO
# =========================================================

def prepare_logo():
    if os.path.exists(LOGO_FILE):
        if os.path.getsize(LOGO_FILE) > 0:
            log("Mevcut logo kullanılacak.")
            return True

    try:
        download_file(LOGO_URL, LOGO_FILE)

        if os.path.getsize(LOGO_FILE) == 0:
            raise RuntimeError("Logo dosyası boş.")

        log("Logo indirildi.")
        return True

    except Exception as error:
        log(f"Logo indirilemedi: {error}")
        return False


# =========================================================
# ALTYAZI
# =========================================================

def prepare_subtitle(subtitle_url):
    if not subtitle_url:
        log("Bu film için harici altyazı belirtilmemiş.")
        return None

    try:
        parsed = urlparse(subtitle_url)

        extension = os.path.splitext(parsed.path)[1].lower()

        if extension not in (".srt", ".vtt", ".ass", ".ssa"):
            extension = ".srt"

        subtitle_file = "maxpremier_subtitle" + extension

        download_file(subtitle_url, subtitle_file)

        if os.path.getsize(subtitle_file) == 0:
            raise RuntimeError("Altyazı dosyası boş.")

        log("Harici altyazı indirildi.")

        return os.path.abspath(subtitle_file)

    except Exception as error:
        log(f"Altyazı indirilemedi: {error}")
        return None


def escape_filter_path(path):
    """
    FFmpeg filtre yolundaki özel karakterleri kaçırır.
    """

    path = os.path.abspath(path)
    path = path.replace("\\", "\\\\")
    path = path.replace(":", "\\:")
    path = path.replace("'", "\\'")
    path = path.replace(",", "\\,")
    path = path.replace("[", "\\[")
    path = path.replace("]", "\\]")

    return path


# =========================================================
# FFMPEG KOMUTU
# =========================================================

def build_ffmpeg_command(item, logo_enabled, subtitle_file, position):
    video_url = item["url"]
    audio_url = item.get("audio_url", "")

    command = ["ffmpeg", "-hide_banner", "-loglevel", "info"]

    # Video girdisi.
    if position > 0:
        command += ["-ss", str(position)]

    command += [
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-i", video_url
    ]

    # Ayrı ses girdisi varsa video ile aynı konumdan başlat.
    audio_input_index = 0

    if audio_url:
        if position > 0:
            command += ["-ss", str(position)]

        command += [
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
            "-i", audio_url
        ]

        audio_input_index = 1

    # Logo girdisi.
    logo_input_index = None

    if logo_enabled:
        logo_input_index = 2 if audio_url else 1
        command += ["-i", LOGO_FILE]

    # Filtre zinciri.
    filters = []

    if subtitle_file:
        escaped_subtitle = escape_filter_path(subtitle_file)

        filters.append(
            f"subtitles=filename='{escaped_subtitle}'"
        )

    if logo_enabled:
        filters.append(
            f"[{logo_input_index}:v]"
            f"scale={LOGO_WIDTH}:-1[logo]"
        )

        filters.append(
            f"[0:v]{','.join(filters) if filters else 'null'}[base]"
        )

        filters.append(
            "[base][logo]overlay=W-w-20:20[vout]"
        )

        command += [
            "-filter_complex",
            ";".join(filters),
            "-map", "[vout]"
        ]

    elif subtitle_file:
        command += [
            "-vf",
            ",".join(filters),
            "-map", "0:v:0"
        ]

    else:
        command += [
            "-map", "0:v:0"
        ]

    # Ses kaynağını doğru girdiden al.
    if audio_url:
        command += [
            "-map", f"{audio_input_index}:a:0?"
        ]
    else:
        command += [
            "-map", "0:a:0?"
        ]

    # Yayın kodlaması.
    command += [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-tune", "zerolatency",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        "-f", "flv",
        STREAM_OUTPUT
    ]

    return command


# =========================================================
# FFMPEG ÇALIŞTIRMA
# =========================================================

def parse_ffmpeg_time(line):
    match = re.search(
        r"time=(\d+):(\d+):(\d+(?:\.\d+)?)",
        line
    )

    if not match:
        return None

    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = float(match.group(3))

    return hours * 3600 + minutes * 60 + seconds


def run_film(item, state, logo_enabled):
    title = item["title"]

    position = float(state.get("position", 0))

    subtitle_file = prepare_subtitle(
        item.get("subtitle_url", "")
    )

    log(f"Film: {title}")
    log(f"Devam konumu: {position:.1f} saniye")

    state["title"] = title
    state["url"] = item["url"]
    state["status"] = "playing"

    save_state(state)

    command = build_ffmpeg_command(
        item=item,
        logo_enabled=logo_enabled,
        subtitle_file=subtitle_file,
        position=position
    )

    log("FFmpeg başlatılıyor...")

    log(
        "Video adresi: "
        + item["url"]
    )

    if item.get("audio_url"):
        log(
            "Ses adresi: "
            + item["audio_url"]
        )

    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            universal_newlines=True
        )

        last_save_time = time.time()
        last_position = position

        for line in process.stderr:
            line = line.rstrip()

            if line:
                print(f"[FFmpeg] {line}", flush=True)

            current_time = parse_ffmpeg_time(line)

            if current_time is not None:
                last_position = current_time

            if time.time() - last_save_time >= 5:
                state["position"] = last_position
                state["status"] = "playing"

                save_state(state)

                last_save_time = time.time()

        return_code = process.wait()

        state["position"] = last_position

        if return_code == 0:
            log(
                f"Film tamamlandı: {title}"
            )

            state["position"] = 0.0
            state["status"] = "completed"

            save_state(state)

            return True

        log(
            f"FFmpeg {return_code} koduyla kapandı. "
            f"Kaydedilen konum: {last_position:.1f} saniye"
        )

        state["status"] = "error"

        save_state(state)

        return False

    except Exception as error:
        log(f"FFmpeg çalıştırma hatası: {error}")

        state["status"] = "error"
        save_state(state)

        return False

    finally:
        if subtitle_file and os.path.exists(subtitle_file):
            try:
                os.remove(subtitle_file)
            except OSError:
                pass


# =========================================================
# ANA PROGRAM
# =========================================================

def main():
    check_ffmpeg()

    state = load_state()

    logo_enabled = prepare_logo()

    playlist = get_m3u_playlist()

    # Kayıtlı indeks geçerli aralıkta değilse başa dön.
    if not 0 <= state["index"] < len(playlist):
        state["index"] = 0
        state["position"] = 0.0

    log("Yayın sistemi başlatıldı.")

    while True:
        try:
            if not playlist:
                log("Oynatma listesi boş.")
                time.sleep(RETRY_SECONDS)
                playlist = get_m3u_playlist()
                continue

            index = state["index"] % len(playlist)
            item = playlist[index]

            success = run_film(
                item=item,
                state=state,
                logo_enabled=logo_enabled
            )

            if success:
                # Film bittiğinde sonraki kayda geç.
                state["index"] = (index + 1) % len(playlist)
                state["position"] = 0.0
                state["status"] = "starting"

                save_state(state)

            else:
                # Hata durumunda aynı film yeniden denenir.
                log(
                    f"{RETRY_SECONDS} saniye sonra "
                    "aynı film yeniden denenecek."
                )

                time.sleep(RETRY_SECONDS)

                # İsteğe bağlı: yayın listesi güncellenmişse
                # yeni listeyi tekrar yükle.
                try:
                    playlist = get_m3u_playlist()

                    if not 0 <= state["index"] < len(playlist):
                        state["index"] = 0
                        state["position"] = 0.0

                except Exception as error:
                    log(f"M3U yeniden okunamadı: {error}")

        except KeyboardInterrupt:
            log("Program kullanıcı tarafından durduruldu.")
            state["status"] = "stopped"
            save_state(state)
            break

        except Exception as error:
            log(f"Genel hata: {error}")

            state["status"] = "error"
            save_state(state)

            time.sleep(RETRY_SECONDS)


if __name__ == "__main__":
    main()
