#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import sys
import time
import os
import re
import json
import requests
from collections import deque

# ===================== AYARLAR =====================
RTMP_URL = "rtmp://ssh101.bozztv.com:1935/ssh101"
STREAM_KEY = os.getenv("STREAM_KEY") or "megatv_@"
RTMP_SERVER = f"{RTMP_URL}/{STREAM_KEY}"

M3U_URL = os.getenv("M3U_URL") or "https://raw.githubusercontent.com/ino8090/0101/refs/heads/main/deneme.m3u"
LOGO_URL = os.getenv("LOGO_URL") or "https://raw.githubusercontent.com/ino8090/0101/refs/heads/main/file_000000007be48210a068edefa7260629.png"

STATE_FILE_NAME = os.getenv("STATE_FILE_NAME", "m.json")
GITHUB_STEP_SUMMARY = os.getenv("GITHUB_STEP_SUMMARY")

STREAM_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
STREAM_REFERER = "https://vidmody.com/"

# Logo ve yazı opaklık ayarları (0.0 - 1.0 arası)
LOGO_OPACITY = float(os.getenv("LOGO_OPACITY", "0.4"))
TEXT_OPACITY = float(os.getenv("TEXT_OPACITY", "0.5"))
BOLD_FONT_PATH = os.getenv("BOLD_FONT_PATH", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")

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
            return 0, 0, ""
        try:
            with open(STATE_FILE_NAME, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("last_index", 0), data.get("last_seconds", 0), data.get("last_url", "")
        except Exception as e:
            print(f"⚠️ Yerel state okuma hatası: {e}")
    return 0, 0, ""

def update_local_state(index, seconds, url=""):
    try:
        data = {"last_index": int(index), "last_seconds": int(seconds), "last_url": url}
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
            # VTT veya SRT içeriğini profesyonel işleme (UTF-8 garantisi)
            content_str = resp.content.decode('utf-8', errors='ignore')
            
            # Eğer dosya WebVTT formatındaysa, FFmpeg'in bazı sürümleri SRT formatını daha stabil okur. 
            # Ancak standart libass tabanlı subtitles filtresi VTT'yi de destekler.
            with open(sub_filename, 'w', encoding='utf-8') as f:
                f.write(content_str)
                
            print(f"✅ Profesyonel altyazı yüklendi ve kodlandı: {sub_url}")
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

def start_m3u_stream():
    print(f"🔧 Kullanılan M3U   : {M3U_URL}")
    print(f"🔧 Kullanılan Logo  : {LOGO_URL}")
    print(f"🔧 State dosyası    : {STATE_FILE_NAME}")
    print(f"🔧 RTMP hedefi      : {RTMP_SERVER}")

    download_logo()

    current_index, last_seconds, last_url = get_local_state()

    consecutive_fast_failures = 0
    FAST_FAIL_THRESHOLD_SECONDS = 20
    MAX_RETRY_DELAY_SECONDS = 120

    while True:
        playlist = get_m3u_playlist(M3U_URL)
        if not playlist:
            time.sleep(10)
            continue

        if current_index >= len(playlist):
            current_index = 0
            last_seconds = 0
            last_url = ""

        current_item = playlist[current_index]
        target_stream_url = current_item["url"]
        film_title = current_item["title"]
        sub_url = current_item.get("sub_url")

        # Altyazıyı profesyonel olarak hazırla
        active_sub_file = download_subtitle(sub_url)

        if last_seconds > 0 and last_url and target_stream_url != last_url:
            print(f"🔄 Bu sıradaki ({current_index + 1}) içeriğin linki değişmiş, video baştan başlatılacak.")
            last_seconds = 0

        last_url = target_stream_url
        write_title_file(film_title)

        print("=" * 60)
        print("📺 Maxanimasyon Profesyonel Canlı Yayın (1080p 25fps - 2500k) Başlatılıyor")
        print(f"🎬 Oynatılan İçerik  : {film_title}")
        print(f"⏱️ Başlangıç Saniyesi: {last_seconds}")
        print(f"🚀 Hedef RTMP       : {RTMP_SERVER}")

        headers_arg = (
            f"User-Agent: {STREAM_USER_AGENT}\r\n"
            f"Referer: https://vidmody.com/\r\n"
            f"Origin: https://vidmody.com\r\n"
        )

        input_options = [
            '-headers', headers_arg,
            '-protocol_whitelist', 'file,http,https,tcp,tls,crypto',
            '-err_detect', 'ignore_err',
            '-analyzeduration', '2000000',
            '-probesize', '2000000',
            '-reconnect', '1',
            '-reconnect_at_eof', '1',
            '-reconnect_streamed', '1',
            '-reconnect_delay_max', '2',
            '-rw_timeout', '10000000'
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

        # Film başlığı drawtext ayarı
        title_drawtext = (
            f"drawtext=textfile='title.txt':reload=1:fontfile='{BOLD_FONT_PATH}':"
            f"fontcolor=white@{TEXT_OPACITY}:fontsize=30:"
            f"x=80:y=main_h-th-67"
        )

        # ==================== PROFESYONEL FİLTRE ZİNCİRİ (FILTER_COMPLEX) ====================
        # 1. Adım: Video standardizasyonu (1920x1080 ölçekleme, pad ve sabit 25 fps)
        filter_steps = [
            '[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,'
            'pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=25[main]'
        ]
        current_v_label = 'main'

        # 2. Adım: Profesyonel Altyazı Entegrasyonu (libass motoru)
        # Sütun (:) ve ters bölü (\) karakterlerinin FFmpeg filter_complex syntax hatasına yol açmaması için yollar güvenli tutulur.
        if active_sub_file and os.path.exists(active_sub_file):
            # force_style ile profesyonel sinema altyazısı görünümü (Beyaz yazı, siyah arka plan/çerçeve, okunabilir boyut)
            sub_style = (
                "FontName=DejaVuSans-Bold,"
                "FontSize=22,"
                "PrimaryColour=&H00FFFFFF,"      # Beyaz renk (HEX: FFFFFF)
                "OutlineColour=&H00000000,"      # Siyah kenar çizgisi
                "BackColour=&H80000000,"         # Hafif yarı saydam gölge arka planı
                "BorderStyle=1,"                 # Çerçeve stili
                "Outline=1,"                     # Kenar kalınlığı
                "Shadow=1,"                      # Gölge
                "Alignment=2"                    # Alt orta hizalama
            )
            # FFmpeg subtitles filtresi Windows/Linux dosya yollarında özel karakter sorununu önlemek için escape edilir
            safe_sub_path = active_sub_file.replace(":", "\\:")
            sub_filter = f"[{current_v_label}]subtitles='{safe_sub_path}':force_style='{sub_style}'[sub_out]"
            filter_steps.append(sub_filter)
            current_v_label = 'sub_out'

        # 3. Adım: Kanal Logosu Entegrasyonu
        if has_logo1:
            logo_inputs = ['-i', 'logo.png']
            logo_filter = f"[{logo1_input_index}:v]scale=-2:91,format=rgba,colorchannelmixer=aa={LOGO_OPACITY}[logo1]"
            filter_steps.append(logo_filter)
            
            overlay_filter = f"[{current_v_label}][logo1]overlay=main_w-overlay_w-104:80[tmp_logo]"
            filter_steps.append(overlay_filter)
            current_v_label = 'tmp_logo'
        else:
            logo_inputs = []

        # 4. Adım: Canlı Başlık Metni (title.txt)
        final_text_filter = f"[{current_v_label}]{title_drawtext}[v]"
        filter_steps.append(final_text_filter)

        filter_str = ";".join(filter_steps)

        # Profesyonel Encode Parametreleri (Canlı Yayın / H.264 / AAC / Sabit Bitrate)
        command = [
            'ffmpeg',
            '-hide_banner',
            '-loglevel', 'info'
        ] + input_args + logo_inputs + [
            '-filter_complex', filter_str,
            '-map', '[v]'
        ] + audio_map + [
            '-c:v', 'libx264',
            '-preset', 'veryfast',
            '-tune', 'zerolatency',         # Düşük gecikmeli profesyonel canlı yayın optimizasyonu
            '-pix_fmt', 'yuv420p',
            '-r', '25',
            '-b:v', '2500k',
            '-maxrate', '2500k',
            '-bufsize', '3000k',
            '-g', '50',                      # 2 saniyede bir Keyframe (Anlık kopmalarda hızlı toparlanma için)
            '-c:a', 'aac',
            '-b:a', '128k',
            '-ac', '2',
            '-ar', '44100',
            '-f', 'flv',
            RTMP_SERVER
        ]

        print("▶ Profesyonel FFmpeg motoru başlatıldı, yayın işleniyor...")

        process = subprocess.Popen(
            command,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )

        last_save_time = time.time()
        last_dashboard_time = time.time()
        current_stream_seconds = last_seconds
        stderr_tail = deque(maxlen=40)

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

                    if now - last_save_time > 30:
                        update_local_state(current_index, current_stream_seconds, target_stream_url)
                        last_save_time = now

                    if now - last_dashboard_time > 30:
                        print_dashboard(film_title, current_index, len(playlist), current_stream_seconds)
                        write_step_summary(film_title, current_index, len(playlist), current_stream_seconds)
                        last_dashboard_time = now

        if process.returncode == 0:
            print("✅ İçerik bitti, sıradakine geçiliyor.")
            write_step_summary(film_title, current_index, len(playlist), current_stream_seconds, status="✅ Bitti, sıradakine geçiliyor")
            current_index += 1
            last_seconds = 0
            last_url = ""
            update_local_state(current_index, 0, "")
            consecutive_fast_failures = 0
        else:
            print(f"⚠️ Yayın koptu (Return Code: {process.returncode}). Aynı saniyeden tekrar denenecek.")
            if stderr_tail:
                print("🧾 FFmpeg son log satırları:")
                for tail_line in stderr_tail:
                    print(f"   {tail_line}")
            write_step_summary(film_title, current_index, len(playlist), current_stream_seconds, status="🔴 Bağlantı koptu, tekrar denenecek")
            
            duration_this_attempt = current_stream_seconds - last_seconds
            if duration_this_attempt < FAST_FAIL_THRESHOLD_SECONDS:
                consecutive_fast_failures += 1
            else:
                consecutive_fast_failures = 0
            last_seconds = current_stream_seconds
            last_url = target_stream_url
            update_local_state(current_index, last_seconds, last_url)

        if consecutive_fast_failures > 0:
            retry_delay = min(5 * (2 ** consecutive_fast_failures), MAX_RETRY_DELAY_SECONDS)
        else:
            retry_delay = 5

        print(f"⚠️ {retry_delay} saniye sonra tekrar bağlanılıyor...")
        time.sleep(retry_delay)

if __name__ == "__main__":
    start_m3u_stream()
