#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import sys
import time
import os
import re
import json
import threading
import queue
import atexit
import requests
from collections import deque

# ===================== AYARLAR =====================
RTMP_URL = "rtmp://ssh101.bozztv.com:1935/ssh101"
STREAM_KEY = os.getenv("STREAM_KEY") or "megatv_@"
RTMP_SERVER = f"{RTMP_URL}/{STREAM_KEY}"

M3U_URL = os.getenv("M3U_URL") or "https://raw.githubusercontent.com/ino8090/0101/refs/heads/main/deneme.m3u"
LOGO_URL = os.getenv("LOGO_URL") or "https://raw.githubusercontent.com/ino8090/0101/refs/heads/main/file_000000007be48210a068edefa7260629.png"

STATE_FILE_NAME = os.getenv("STATE_FILE_NAME", "mega.json")
GITHUB_STEP_SUMMARY = os.getenv("GITHUB_STEP_SUMMARY")

STREAM_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
STREAM_REFERER = "https://vidmody.com/"

# Logo ve yazı opaklık ayarları (0.0 - 1.0 arası)
LOGO_OPACITY = float(os.getenv("LOGO_OPACITY", "0.4"))
TEXT_OPACITY = float(os.getenv("TEXT_OPACITY", "0.5"))
BOLD_FONT_PATH = os.getenv("BOLD_FONT_PATH", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")

# Encode hızı (CPU yetmiyorsa superfast/ultrafast kullan)
X264_PRESET = os.getenv("X264_PRESET", "superfast")

def format_hms(total_seconds):
    """Saniyeyi SS:DD:SS formatına çevirir."""
    total_seconds = int(total_seconds)
    hrs = total_seconds // 3600
    mins = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hrs:02d}:{mins:02d}:{secs:02d}"

def get_local_state():
    if os.path.exists(STATE_FILE_NAME):
        if os.path.getsize(STATE_FILE_NAME) == 0:
            return 0, 0, "", ""
        try:
            with open(STATE_FILE_NAME, "r", encoding="utf-8") as f:
                data = json.load(f)
                return (data.get("last_index", 0), data.get("last_seconds", 0),
                        data.get("last_url", ""), data.get("last_title", ""))
        except Exception as e:
            print(f"⚠️ Yerel state okuma hatası: {e}")
    return 0, 0, "", ""

def update_local_state(index, seconds, url="", title=""):
    try:
        data = {"last_index": int(index), "last_seconds": int(seconds), "last_url": url, "last_title": title}
        with open(STATE_FILE_NAME, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Yerel state yazma hatası: {e}")

def get_m3u_playlist(m3u_url):
    """M3U dosyasını parse eder, #EXTVLCOPT:sub-url= etiketlerini yakalar."""
    try:
        headers = {
            'User-Agent': STREAM_USER_AGENT,
            'Referer': STREAM_REFERER
        }
        response = requests.get(m3u_url, headers=headers, timeout=15)
        if response.status_code == 200:
            lines = response.text.splitlines()
            playlist = []
            pending_title = None
            pending_sub_url = None
            
            for raw_line in lines:
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith('#EXTINF'):
                    match = re.search(r',(.+)$', line)
                    pending_title = match.group(1).strip() if match else None
                elif line.startswith('#EXTVLCOPT:sub-url='):
                    pending_sub_url = line.split('=', 1)[1].strip()
                elif not line.startswith('#') and line.startswith('http'):
                    title = pending_title or os.path.basename(line.split('?')[0])
                    playlist.append({
                        "url": line, 
                        "title": title, 
                        "sub_url": pending_sub_url
                    })
                    pending_title = None
                    pending_sub_url = None
            return playlist
    except Exception as e:
        print(f"⚠️ M3U çekme hatası: {e}")
    return [{"url": m3u_url, "title": os.path.basename(m3u_url), "sub_url": None}]

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

def download_subtitle(sub_url):
    """Altyazıyı indirir ve FFmpeg'in sorunsuz okuması için UTF-8 formatında kaydeder."""
    sub_filename = 'current_subtitle.srt'
    if not sub_url:
        if os.path.exists(sub_filename):
            os.remove(sub_filename)
        return None
    
    try:
        headers = {'User-Agent': STREAM_USER_AGENT}
        resp = requests.get(sub_url, headers=headers, timeout=15)
        if resp.status_code == 200 and len(resp.content) > 0:
            content_str = resp.content.decode('utf-8', errors='ignore')
            with open(sub_filename, 'w', encoding='utf-8') as f:
                f.write(content_str)
            print(f"✅ Altyazı yüklendi ve kodlandı: {sub_url}")
            return sub_filename
    except Exception as e:
        print(f"⚠️ Altyazı indirme/işleme hatası: {e}")
    
    if os.path.exists(sub_filename):
        os.remove(sub_filename)
    return None

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

def _stderr_reader(pipe, q):
    """FFmpeg stderr'ini ayrı thread'de okur; ana döngü hiçbir zaman readline'da kilitlenmez."""
    try:
        for ln in iter(pipe.readline, ''):
            q.put(ln)
    except Exception:
        pass
    finally:
        q.put(None)  # bitiş işareti

def get_media_duration(url):
    """ffprobe ile içeriğin toplam süresini (sn) bulur; bulunamazsa None döner."""
    video_url = url.split(";", 1)[0].strip()
    headers_arg = (
        f"User-Agent: {STREAM_USER_AGENT}\r\n"
        f"Referer: {STREAM_REFERER}\r\n"
        f"Origin: https://vidmody.com\r\n"
    )
    try:
        out = subprocess.run(
            ['ffprobe', '-v', 'error', '-headers', headers_arg,
             '-protocol_whitelist', 'file,http,https,tcp,tls,crypto',
             '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', video_url],
            capture_output=True, text=True, timeout=40
        )
        val = float(out.stdout.strip().splitlines()[0])
        return val if val > 0 else None
    except Exception:
        return None

def _publisher_reader(pipe, state):
    """Publisher FFmpeg'in stderr'ini okur; son satırları ve son ilerleme zamanını tutar."""
    try:
        for ln in iter(pipe.readline, ''):
            ln = ln.rstrip()
            state['tail'].append(ln)
            if 'time=' in ln:
                state['last_progress'] = time.time()
    except Exception:
        pass

def stop_publisher(p):
    if p is None:
        return
    try:
        if p.stdin:
            p.stdin.close()
    except Exception:
        pass
    try:
        p.terminate()
        p.wait(timeout=3)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass

def start_publisher():
    """
    Kalıcı RTMP bağlantısı: tek bir FFmpeg süreci stdin'den MPEG-TS okur ve RTMP'ye kopyalar (-c copy).
    Filmler arası geçişte sadece 'film FFmpeg'i' değişir, bu süreç ve RTMP bağlantısı açık kalır.
    """
    cmd = [
        'ffmpeg',
        '-hide_banner',
        '-loglevel', 'info',
        '-fflags', '+genpts',
        '-analyzeduration', '3000000',
        '-probesize', '3000000',
        # Film değişince zaman damgaları başa sarar; 1 sn'den büyük sıçramalar otomatik düzeltilir
        '-dts_delta_threshold', '1',
        '-f', 'mpegts',
        '-i', 'pipe:0',
        '-c', 'copy',
        '-flvflags', 'no_duration_filesize',
        '-f', 'flv',
        RTMP_SERVER
    ]
    p = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        universal_newlines=True,
        bufsize=1
    )
    state = {'tail': deque(maxlen=40), 'last_progress': time.time()}
    threading.Thread(target=_publisher_reader, args=(p.stderr, state), daemon=True).start()
    atexit.register(stop_publisher, p)
    print("🔌 Kalıcı RTMP publisher başlatıldı (filmler arası kapanmaz).")
    return p, state

def start_m3u_stream():
    print(f"🔧 Kullanılan M3U   : {M3U_URL}")
    print(f"🔧 Kullanılan Logo  : {LOGO_URL}")
    print(f"🔧 State dosyası    : {STATE_FILE_NAME}")
    print(f"🔧 RTMP hedefi      : {RTMP_SERVER}")

    download_logo()

    current_index, last_seconds, last_url, last_title = get_local_state()
    print(f"💾 Kayıtlı durum    : sıra={current_index + 1}, süre={format_hms(last_seconds)}, film='{last_title}'")

    duration_cache = {}
    EARLY_END_TOLERANCE_SECONDS = 45
    publisher = None
    pub_state = None
    force_restart_publisher = False
    freeze_streak = 0

    consecutive_fast_failures = 0
    FAST_FAIL_THRESHOLD_SECONDS = 20
    MAX_RETRY_DELAY_SECONDS = 120

    while True:
        playlist = get_m3u_playlist(M3U_URL)
        if not playlist:
            time.sleep(10)
            continue

        # Kayıtlı filmi ADIYLA doğrula (link değişmiş olsa da önemli değil)
        if last_seconds > 0 and last_title:
            if current_index >= len(playlist) or playlist[current_index]["title"] != last_title:
                found_index = next((i for i, it in enumerate(playlist) if it["title"] == last_title), None)
                if found_index is not None:
                    print(f"🔎 '{last_title}' playlist'te {found_index + 1}. sıraya taşınmış, oradan devam edilecek.")
                    current_index = found_index
                else:
                    print(f"🔄 '{last_title}' adlı film playlist'te yok (ad değişmiş), {current_index + 1}. sıradaki film baştan başlatılacak.")
                    last_seconds = 0

        if current_index >= len(playlist):
            current_index = 0
            last_seconds = 0
            last_url = ""
            last_title = ""

        current_item = playlist[current_index]
        target_stream_url = current_item["url"]
        film_title = current_item["title"]
        sub_url = current_item.get("sub_url")

        active_sub_file = download_subtitle(sub_url)

        # NOT: Link değişmesi önemli değil; baştan başlatma kararı sadece film ADINA göre verilir.

        if last_seconds > 0:
            print(f"⏩ '{film_title}' için {current_index + 1}. sıradan, {format_hms(last_seconds)} konumundan devam edilecek.")

        last_url = target_stream_url
        last_title = film_title

        # Film süresini bul (erken biten yayını "film bitti" sanmamak için)
        if target_stream_url not in duration_cache:
            duration_cache[target_stream_url] = get_media_duration(target_stream_url)
        film_duration = duration_cache[target_stream_url]
        if film_duration:
            print(f"🕒 Film süresi      : {format_hms(film_duration)}")
        write_title_file(film_title)

        print("=" * 60)
        print("📺 Maxanimasyon Profesyonel Kesintisiz Yayın Motoru Başlatılıyor")
        print(f"🎬 Oynatılan İçerik  : {film_title}")
        print(f"⏱️ Başlangıç Saniyesi: {last_seconds}")
        print(f"🚀 Hedef RTMP       : {RTMP_SERVER}")

        headers_arg = (
            f"User-Agent: {STREAM_USER_AGENT}\r\n"
            f"Referer: https://vidmody.com/\r\n"
            f"Origin: https://vidmody.com\r\n"
        )

        # -readrate 1: -re yerine, gerçek zamanlı okuma hızı (FFmpeg 5.0+ gerekir)
        # NOT: '-reconnect_at_eof' kaldırıldı (film sonunda sonsuz yeniden bağlanma/donma yapabiliyordu)
        input_options = [
            '-readrate', '1',
            '-fflags', '+genpts+discardcorrupt',
            '-headers', headers_arg,
            '-protocol_whitelist', 'file,http,https,tcp,tls,crypto',
            '-err_detect', 'ignore_err',
            '-analyzeduration', '3000000',
            '-probesize', '3000000',
            '-thread_queue_size', '512',
            '-reconnect', '1',
            '-reconnect_streamed', '1',
            '-reconnect_delay_max', '5',
            '-rw_timeout', '15000000'
        ]

        if ";" in target_stream_url:
            video_url, audio_url = target_stream_url.split(";", 1)
            video_url = video_url.strip()
            audio_url = audio_url.strip()

            input_args = (
                ['-ss', str(last_seconds)] + input_options + ['-i', video_url] +
                ['-ss', str(last_seconds)] + input_options + ['-i', audio_url]
            )
            audio_map = ['-map', '1:a:0?']
            logo1_input_index = 2
        else:
            input_args = ['-ss', str(last_seconds)] + input_options + ['-i', target_stream_url]
            audio_map = ['-map', '0:a:0?']
            logo1_input_index = 1

        print("=" * 60)
        print_dashboard(film_title, current_index, len(playlist), last_seconds, status="🟡 Başlatılıyor")
        write_step_summary(film_title, current_index, len(playlist), last_seconds, status="🟡 Başlatılıyor")

        has_logo1 = os.path.exists('logo.png') and os.path.getsize('logo.png') > 0

        # reload=1 kaldırıldı: başlık yayın boyunca değişmiyor, her karede diskten okumak gereksiz yük
        title_drawtext = (
            f"drawtext=textfile='title.txt':fontfile='{BOLD_FONT_PATH}':"
            f"fontcolor=white@{TEXT_OPACITY}:fontsize=30:"
            f"x=80:y=main_h-th-67"
        )

        filter_steps = [
            '[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,'
            'pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=25[main]'
        ]
        current_v_label = 'main'

        if active_sub_file and os.path.exists(active_sub_file):
            sub_style = (
                "FontName=DejaVuSans-Bold,"
                "FontSize=22,"
                "PrimaryColour=&H00FFFFFF,"
                "OutlineColour=&H00000000,"
                "BackColour=&H80000000,"
                "BorderStyle=1,"
                "Outline=1,"
                "Shadow=1,"
                "Alignment=2"
            )
            safe_sub_path = active_sub_file.replace(":", "\\:")
            sub_filter = f"[{current_v_label}]subtitles='{safe_sub_path}':force_style='{sub_style}'[sub_out]"
            filter_steps.append(sub_filter)
            current_v_label = 'sub_out'

        if has_logo1:
            logo_inputs = ['-i', 'logo.png']
            logo_filter = f"[{logo1_input_index}:v]scale=-2:91,format=rgba,colorchannelmixer=aa={LOGO_OPACITY}[logo1]"
            filter_steps.append(logo_filter)
            
            overlay_filter = f"[{current_v_label}][logo1]overlay=main_w-overlay_w-104:80[tmp_logo]"
            filter_steps.append(overlay_filter)
            current_v_label = 'tmp_logo'
        else:
            logo_inputs = []

        final_text_filter = f"[{current_v_label}]{title_drawtext}[v]"
        filter_steps.append(final_text_filter)

        filter_str = ";".join(filter_steps)

        command = [
            'ffmpeg',
            '-hide_banner',
            '-loglevel', 'info'
        ] + input_args + logo_inputs + [
            '-filter_complex', filter_str,
            '-map', '[v]'
        ] + audio_map + [
            '-c:v', 'libx264',
            '-preset', X264_PRESET,
            '-tune', 'zerolatency',
            '-pix_fmt', 'yuv420p',
            '-r', '25',
            '-b:v', '2500k',
            '-maxrate', '2500k',
            '-bufsize', '3000k',
            '-g', '50',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-ac', '2',
            '-ar', '44100',
            '-af', 'aresample=async=1:first_pts=0',
            '-max_muxing_queue_size', '1024',
            '-flush_packets', '1',
            '-mpegts_flags', '+initial_discontinuity',
            '-f', 'mpegts',
            'pipe:1'
        ]

        # --- Kalıcı RTMP publisher: yoksa/ölmüşse başlat, yaşıyorsa DOKUNMA ---
        if publisher is None or publisher.poll() is not None or force_restart_publisher:
            stop_publisher(publisher)
            publisher, pub_state = start_publisher()
            force_restart_publisher = False

        froze = False
        publisher_died = False

        print("▶ FFmpeg ve Watchdog Güvenlik Katmanı Başlatıldı...")

        # Film FFmpeg'i çıktısını doğrudan publisher'ın stdin'ine yazar
        process = subprocess.Popen(
            command,
            stderr=subprocess.PIPE,
            stdout=publisher.stdin,
            universal_newlines=True,
            bufsize=1
        )

        # stderr ayrı thread'de okunuyor -> watchdog artık kilitlenmiyor
        line_queue = queue.Queue()
        threading.Thread(target=_stderr_reader, args=(process.stderr, line_queue), daemon=True).start()

        last_save_time = time.time()
        last_dashboard_time = time.time()
        last_progress_time = time.time()
        current_stream_seconds = last_seconds
        last_played_seconds = -1.0
        last_speed = None
        stderr_tail = deque(maxlen=40)
        
        FREEZE_TIMEOUT_SECONDS = 50 

        while True:
            try:
                line = line_queue.get(timeout=1)
            except queue.Empty:
                line = ""
            else:
                if line is None:
                    break  # FFmpeg kapandı
                stderr_tail.append(line.rstrip())

            if line and "time=" in line:
                time_match = re.search(r'time=(\d+):(\d+):(\d+\.\d+)', line)
                if time_match:
                    hrs, mins, secs = time_match.groups()
                    played_seconds = int(hrs) * 3600 + int(mins) * 60 + float(secs)
                    current_stream_seconds = last_seconds + played_seconds

                    speed_match = re.search(r'speed=\s*([\d.]+)x', line)
                    if speed_match:
                        last_speed = float(speed_match.group(1))

                    now = time.time()

                    # Sadece süre gerçekten ilerlediyse "ilerleme var" say
                    if played_seconds > last_played_seconds:
                        last_played_seconds = played_seconds
                        last_progress_time = now

                    if now - last_save_time > 30:
                        update_local_state(current_index, current_stream_seconds, target_stream_url, film_title)
                        last_save_time = now

                    if now - last_dashboard_time > 30:
                        print_dashboard(film_title, current_index, len(playlist), current_stream_seconds)
                        if last_speed is not None:
                            warn = "  ⚠️ CPU yetmiyor olabilir (1.0x altı)" if last_speed < 0.97 else ""
                            print(f"⚡ Encode hızı: {last_speed:.2f}x{warn}")
                        write_step_summary(film_title, current_index, len(playlist), current_stream_seconds)
                        last_dashboard_time = now

            if publisher.poll() is not None:
                publisher_died = True
                print("⚠️ UYARI: RTMP publisher süreci kapandı. Konum kaydedilip yeniden bağlanılacak...")
                print("🧾 Publisher son log satırları:")
                for t in list(pub_state['tail'])[-15:]:
                    print(f"   {t}")
                try:
                    process.terminate()
                    process.wait(timeout=3)
                except Exception:
                    process.kill()
                break

            if time.time() - last_progress_time > FREEZE_TIMEOUT_SECONDS:
                froze = True
                print(f"⚠️ UYARI: FFmpeg {FREEZE_TIMEOUT_SECONDS} saniyedir veri akışı sağlayamadı (Donma tespit edildi!). Süreç sonlandırılıyor...")
                try:
                    process.terminate()
                    process.wait(timeout=3)
                except Exception:
                    process.kill()
                break

        try:
            process.wait(timeout=10)
        except Exception:
            process.kill()
            process.wait()

        # FFmpeg 0 koduyla çıktı ama film gerçekten bitmedi mi? (kaynak bağlantıyı erken kapatmış olabilir)
        early_end = False
        if process.returncode == 0 and not publisher_died:
            if film_duration:
                early_end = current_stream_seconds < film_duration - EARLY_END_TOLERANCE_SECONDS
            else:
                early_end = (current_stream_seconds - last_seconds) < FAST_FAIL_THRESHOLD_SECONDS
            if early_end:
                shown_total = format_hms(film_duration) if film_duration else "?"
                print(f"⚠️ İçerik ERKEN bitti ({format_hms(current_stream_seconds)} / {shown_total}); sıradakine GEÇİLMEYECEK, kaldığı yerden devam edilecek.")

        if process.returncode == 0 and not publisher_died and not early_end:
            print("✅ İçerik bitti, sıradakine geçiliyor.")
            write_step_summary(film_title, current_index, len(playlist), current_stream_seconds, status="✅ Bitti, sıradakine geçiliyor")
            current_index += 1
            last_seconds = 0
            last_url = ""
            last_title = ""
            update_local_state(current_index, 0, "", "")
            consecutive_fast_failures = 0
            freeze_streak = 0
            continue  # RTMP açık kalsın: beklemeden sıradaki filme geç
        else:
            print(f"⚠️ Yayın koptu veya donma nedeniyle yeniden başlatılıyor (Return Code: {process.returncode}).")
            if stderr_tail:
                print("🧾 FFmpeg son log satırları:")
                for tail_line in stderr_tail:
                    print(f"   {tail_line}")
            write_step_summary(film_title, current_index, len(playlist), current_stream_seconds, status="🔴 Yayın dondu/koptu, kaldığı yerden tekrar denenecek")
            
            duration_this_attempt = current_stream_seconds - last_seconds
            if duration_this_attempt < FAST_FAIL_THRESHOLD_SECONDS:
                consecutive_fast_failures += 1
            else:
                consecutive_fast_failures = 0
            
            last_seconds = current_stream_seconds
            last_url = target_stream_url
            update_local_state(current_index, last_seconds, last_url, film_title)

            # Üst üste donma: sorun RTMP tarafında olabilir, publisher'ı da yenile
            if froze:
                freeze_streak += 1
            elif duration_this_attempt >= 60:
                freeze_streak = 0
            if freeze_streak >= 2:
                print("🔁 Üst üste donma tespit edildi: RTMP publisher bağlantısı da yenilenecek.")
                force_restart_publisher = True
                freeze_streak = 0

        if consecutive_fast_failures > 0:
            retry_delay = min(5 * (2 ** consecutive_fast_failures), MAX_RETRY_DELAY_SECONDS)
        else:
            retry_delay = 5

        print(f"⚠️ {retry_delay} saniye sonra aynı yerden tekrar bağlanılıyor...")
        time.sleep(retry_delay)

if __name__ == "__main__":
    start_m3u_stream()
