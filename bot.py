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
import random
from collections import deque

# ===================== AYARLAR =====================
RTMP_URL = "rtmp://ssh101.bozztv.com:1935/ssh101"
STREAM_KEY = os.getenv("STREAM_KEY") or "digimax2"
RTMP_SERVER = f"{RTMP_URL}/{STREAM_KEY}"

M3U_URL = os.getenv("M3U_URL") or "https://raw.githubusercontent.com/gistmedya/0101/refs/heads/main/deneme.m3u"
LOGO_URL = os.getenv("LOGO_URL") or "https://raw.githubusercontent.com/gistmedya/0101/refs/heads/main/1792821507087.png"

STATE_FILE_NAME = os.getenv("STATE_FILE_NAME", "digi.json")
GITHUB_STEP_SUMMARY = os.getenv("GITHUB_STEP_SUMMARY")

STREAM_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
DEFAULT_REFERER = os.getenv("STREAM_REFERER", "https://ha.vixolity.com/")
STREAM_COOKIE = os.getenv("STREAM_COOKIE", "")

# ===================== PROXY AYARLARI =====================
# Manuel proxy: env ile verilebilir
# Örnek: PROXY_URL="http://user:pass@1.2.3.4:8080"
PROXY_URL = os.getenv("PROXY_URL", "").strip()

# Otomatik TR proxy listesi çekme (ücretsiz, dengesiz ama bedava)
AUTO_FETCH_PROXY = os.getenv("AUTO_FETCH_PROXY", "1") == "1"
PROXY_LIST_FILE = "tr_proxies.txt"
PROXY_TEST_URL = "https://load.harborstatic.lol/hls/9/cdn920/kLDdlmFhAXHDoNfs09JgU1oTL2BpIzJ27xnnv4KX.mp4/index.m3u8"  # test URL
PROXY_TEST_REFERER = "https://vidmody.com/"

# Logo ve yazı
LOGO_OPACITY = float(os.getenv("LOGO_OPACITY", "1.0"))
TEXT_OPACITY = float(os.getenv("TEXT_OPACITY", "0.0"))
BOLD_FONT_PATH = os.getenv("BOLD_FONT_PATH", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
DECODER_THREADS = os.getenv("DECODER_THREADS", "1")

WATCHDOG_STARTUP_SECONDS = int(os.getenv("WATCHDOG_STARTUP_SECONDS", "90"))
WATCHDOG_TIMEOUT_SECONDS = int(os.getenv("WATCHDOG_TIMEOUT_SECONDS", "45"))
END_TOLERANCE_SECONDS = 15
SEEK_BACKOFF_SECONDS = 10
LINK_CHANGE_REWIND_SECONDS = int(os.getenv("LINK_CHANGE_REWIND_SECONDS", "15"))

# ===================== REFERER HARİTASI =====================
REFERER_MAP = {
    "harborstatic.lol": "https://vidmody.com/",
    "vixolity.com": "https://ha.vixolity.com/",
    "5455456.xyz": "https://ha.vixolity.com/",
    "vidmody.com": "https://vidmody.com/",
    "mediafire.com": "https://www.mediafire.com/",
    "harborcdn.lol": "https://vidmody.com/",
    "harborstream.lol": "https://vidmody.com/",
}

# ===================== AKTİF PROXY =====================
_working_proxy = {"url": PROXY_URL if PROXY_URL else None, "verified": False}


def format_hms(total_seconds):
    total_seconds = max(0, int(total_seconds))
    hrs = total_seconds // 3600
    mins = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hrs:02d}:{mins:02d}:{secs:02d}"


def guess_referer(url):
    for domain, ref in REFERER_MAP.items():
        if domain in url:
            return ref
    return DEFAULT_REFERER


def build_headers_arg(referer):
    h = (
        f"User-Agent: {STREAM_USER_AGENT}\r\n"
        f"Referer: {referer}\r\n"
        f"Origin: {referer.rstrip('/')}\r\n"
        f"Accept: */*\r\n"
        f"Accept-Language: tr-TR,tr;q=0.9,en;q=0.8\r\n"
        f"Connection: keep-alive\r\n"
    )
    if STREAM_COOKIE:
        h += f"Cookie: {STREAM_COOKIE}\r\n"
    return h


# ===================== PROXY YÖNETİMİ =====================
def fetch_tr_proxies():
    """Ücretsiz TR proxy listesi çek (birden fazla kaynaktan)."""
    proxies = set()

    # Kaynak 1: proxyscrape.com
    try:
        r = requests.get(
            "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&country=tr&proxy_format=protocolipport&format=text",
            timeout=15,
            headers={"User-Agent": STREAM_USER_AGENT}
        )
        if r.status_code == 200:
            for line in r.text.splitlines():
                line = line.strip()
                if line and not line.startswith('#'):
                    if not line.startswith('http'):
                        line = f"http://{line}"
                    proxies.add(line)
            print(f"📥 proxyscrape: {len(proxies)} TR proxy")
    except Exception as e:
        print(f"⚠️ proxyscrape hatası: {e}")

    # Kaynak 2: proxy-list.download
    try:
        r = requests.get(
            "https://www.proxy-list.download/api/v1/get?type=http&country=TR",
            timeout=15,
            headers={"User-Agent": STREAM_USER_AGENT}
        )
        if r.status_code == 200:
            before = len(proxies)
            for line in r.text.splitlines():
                line = line.strip()
                if line and ":" in line and not line.startswith('#'):
                    if not line.startswith('http'):
                        line = f"http://{line}"
                    proxies.add(line)
            print(f"📥 proxy-list.download: {len(proxies) - before} yeni TR proxy")
    except Exception as e:
        print(f"⚠️ proxy-list.download hatası: {e}")

    return list(proxies)


def test_proxy(proxy_url, test_url, referer):
    """Proxy gerçekten çalışıyor mu ve hedef URL'ye erişebiliyor mu?"""
    try:
        headers = {
            "User-Agent": STREAM_USER_AGENT,
            "Referer": referer,
            "Range": "bytes=0-1023",
            "Accept": "*/*",
        }
        r = requests.get(
            test_url,
            headers=headers,
            proxies={"http": proxy_url, "https": proxy_url},
            timeout=12,
            stream=True,
            allow_redirects=True,
        )
        ok = r.status_code in (200, 206)
        r.close()
        return ok, r.status_code
    except Exception as e:
        return False, str(e)[:80]


def find_working_proxy(sample_url=None, referer=None):
    """Çalışan bir TR proxy bul. Öncelik: env PROXY_URL > otomatik."""
    global _working_proxy

    # Zaten çalışan proxy varsa tekrar test et
    if _working_proxy["url"]:
        ok, status = test_proxy(_working_proxy["url"], sample_url or PROXY_TEST_URL, referer or PROXY_TEST_REFERER)
        if ok:
            print(f"✅ Mevcut proxy çalışıyor: {_working_proxy['url']}")
            _working_proxy["verified"] = True
            return _working_proxy["url"]
        print(f"⚠️ Mevcut proxy artık çalışmıyor: {_working_proxy['url']} (durum: {status})")

    # Manuel proxy test
    if PROXY_URL:
        print(f"🔍 Manuel proxy test ediliyor: {PROXY_URL}")
        ok, status = test_proxy(PROXY_URL, sample_url or PROXY_TEST_URL, referer or PROXY_TEST_REFERER)
        if ok:
            print(f"✅ Manuel proxy çalışıyor: {PROXY_URL}")
            _working_proxy = {"url": PROXY_URL, "verified": True}
            return PROXY_URL
        print(f"❌ Manuel proxy çalışmıyor (durum: {status})")

    if not AUTO_FETCH_PROXY:
        return None

    # Otomatik çek
    print("🌐 Ücretsiz TR proxy listesi çekiliyor...")
    candidates = fetch_tr_proxies()
    if not candidates:
        print("⚠️ Hiç TR proxy bulunamadı.")
        return None

    # Dosyadan önceki çalışanları yükle
    cached = []
    if os.path.exists(PROXY_LIST_FILE):
        try:
            with open(PROXY_LIST_FILE) as f:
                cached = [l.strip() for l in f if l.strip()]
            print(f"💾 Önbellekten {len(cached)} proxy yüklendi.")
        except Exception:
            pass

    # Önbellek + taze liste birleştir, karıştır
    all_proxies = list(dict.fromkeys(cached + candidates))
    random.shuffle(all_proxies)

    test_target = sample_url or PROXY_TEST_URL
    test_ref = referer or PROXY_TEST_REFERER
    print(f"🧪 {len(all_proxies)} proxy test edilecek (hedef: {test_target[:80]})")

    # Paralel test (hız için)
    results = []
    lock = threading.Lock()

    def try_proxy(p):
        ok, status = test_proxy(p, test_target, test_ref)
        with lock:
            if ok:
                results.append(p)
                print(f"  ✅ {p}")

    threads = []
    for p in all_proxies[:60]:  # ilk 60 tanesini dene (zaman için)
        t = threading.Thread(target=try_proxy, args=(p,), daemon=True)
        t.start()
        threads.append(t)
        # Aynı anda 15 thread'den fazla açma
        if len(threads) % 15 == 0:
            time.sleep(0.5)
        # Çalışan bulduysak erken çık
        with lock:
            if len(results) >= 3:
                break

    # Thread'lerin bitmesini bekle (max 60s)
    deadline = time.time() + 60
    for t in threads:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        t.join(timeout=remaining)

    if results:
        chosen = results[0]
        print(f"🎯 Çalışan proxy seçildi: {chosen}")
        _working_proxy = {"url": chosen, "verified": True}
        # Önbelleğe kaydet
        try:
            with open(PROXY_LIST_FILE, "w") as f:
                for p in results:
                    f.write(p + "\n")
        except Exception:
            pass
        return chosen

    print("❌ Hiçbir proxy hedef URL'ye erişemedi.")
    return None


def get_proxy():
    return _working_proxy.get("url")


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
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, bufsize=0
        )
        self.started_at = time.time()
        threading.Thread(target=self._drain, args=(self.proc,), daemon=True).start()

    def _drain(self, proc):
        try:
            for raw in iter(proc.stderr.readline, b''):
                line = raw.decode('utf-8', 'replace').rstrip()
                self.tail.append(line)
                if any(x in line.lower() for x in ['error', 'failed', 'refused', 'cannot']):
                    print(f"🚨 RTMP: {line}")
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
    d = get_video_duration_ffprobe(url)
    if d > 0:
        _duration_cache[url] = d


def get_video_duration_ffprobe(video_url):
    """Proxy varsa onu kullan."""
    referers_to_try = [guess_referer(video_url), DEFAULT_REFERER, ""]
    proxy = get_proxy()

    for ref in referers_to_try:
        cmd = [
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            '-rw_timeout', '10000000',
        ]
        if proxy:
            cmd += ['-http_proxy', proxy]
        if ref:
            cmd += ['-headers', build_headers_arg(ref)]
        cmd.append(video_url)

        try:
            result = subprocess.run(cmd, capture_output=True, timeout=25)
            if result.returncode == 0:
                raw = result.stdout.decode('utf-8', 'replace').strip()
                try:
                    duration = float(raw.splitlines()[-1].strip())
                    if duration > 0:
                        print(f"⏱️ ffprobe süre: {format_hms(duration)} [Ref: {ref or 'YOK'}, Proxy: {'VAR' if proxy else 'YOK'}]")
                        return duration
                except (ValueError, IndexError):
                    pass
                return 0.0
            else:
                err = result.stderr.decode('utf-8', 'replace').strip()
                short = err.splitlines()[-1] if err else "bilinmeyen"
                print(f"⚠️ ffprobe [{ref or 'YOK'}] → {short[:150]}")
                if "403" not in short.lower() and "forbidden" not in short.lower():
                    return 0.0
        except subprocess.TimeoutExpired:
            print(f"⚠️ ffprobe timeout [Ref: {ref or 'YOK'}]")
        except Exception as e:
            print(f"⚠️ ffprobe hata: {e}")
    return 0.0


def is_forbidden_error(stderr_lines):
    text = "\n".join(stderr_lines).lower()
    return "403" in text or "forbidden" in text


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
                return (data.get("last_index", 0), data.get("last_seconds", 0),
                        data.get("last_url", ""), data.get("last_title", ""))
        except Exception as e:
            print(f"⚠️ state okuma: {e}")
    return 0, 0, "", ""


def update_local_state(index, seconds, url="", title=""):
    tmp = f"{STATE_FILE_NAME}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"last_index": int(index), "last_seconds": int(seconds),
                       "last_url": url, "last_title": title}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE_NAME)
        print(f"💾 Konum: İndeks={index}, Saniye={int(seconds)}")
    except Exception as e:
        print(f"⚠️ state yazma: {e}")


def get_m3u_playlist(m3u_url):
    try:
        headers = {'User-Agent': STREAM_USER_AGENT, 'Referer': DEFAULT_REFERER}
        response = requests.get(m3u_url, headers=headers, timeout=15)
        if response.status_code == 200:
            lines = response.text.splitlines()
            playlist, pending_title = [], None
            for raw in lines:
                line = raw.strip()
                if not line:
                    continue
                if line.startswith('#EXTINF'):
                    m = re.search(r',(.+)$', line)
                    pending_title = m.group(1).strip() if m else None
                elif not line.startswith('#') and line.startswith('http'):
                    title = pending_title or os.path.basename(line.split('?')[0])
                    playlist.append({"url": line, "title": title})
                    pending_title = None
            return playlist
    except Exception as e:
        print(f"⚠️ M3U çekme: {e}")
    return [{"url": m3u_url, "title": os.path.basename(m3u_url)}]


def download_logo():
    try:
        r = requests.get(LOGO_URL, headers={'User-Agent': STREAM_USER_AGENT}, timeout=15)
        if r.status_code == 200 and len(r.content) > 0:
            with open('logo.png', 'wb') as f:
                f.write(r.content)
            print("✅ Logo indirildi.")
    except Exception as e:
        print(f"⚠️ logo: {e}")


def write_title_file(title):
    try:
        with open('title.txt', 'w', encoding='utf-8') as f:
            f.write(title)
    except Exception as e:
        print(f"⚠️ title: {e}")


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
        proxy_txt = get_proxy() or "yok"
        content = (
            "## 📺 Canlı Yayın Durumu\n\n"
            "| Alan | Değer |\n|---|---|\n"
            f"| 🎬 İçerik | {title} |\n"
            f"| 🔢 Sıra | {index + 1} / {playlist_len} |\n"
            f"| ⏱️ Geçen | {format_hms(seconds)} |\n"
            f"| 📡 Durum | {status} |\n"
            f"| 🌐 Proxy | {proxy_txt} |\n"
            f"| 🕒 Güncelleme | {time.strftime('%Y-%m-%d %H:%M:%S')} |\n"
        )
        with open(GITHUB_STEP_SUMMARY, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        print(f"⚠️ summary: {e}")


def is_valid_stream_url(url):
    for p in ["mediafire.com/file/", "drive.google.com/file/", "youtube.com/watch", "youtu.be/", "dropbox.com/s/"]:
        if p in url:
            return False
    return True


def start_m3u_stream():
    print(f"🔧 M3U             : {M3U_URL}")
    print(f"🔧 Logo            : {LOGO_URL}")
    print(f"🔧 State           : {STATE_FILE_NAME}")
    print(f"🔧 RTMP            : {RTMP_SERVER}")
    print(f"🔧 Referer         : {DEFAULT_REFERER}")
    print(f"🔧 Manuel Proxy    : {PROXY_URL or 'yok'}")
    print(f"🔧 Otomatik Proxy  : {'aktif' if AUTO_FETCH_PROXY else 'kapalı'}")

    download_logo()
    current_index, last_seconds, last_url, last_title = get_local_state()

    # ✅ Başlangıçta proxy bul
    print("🌐 Çalışan TR proxy aranıyor...")
    find_working_proxy()

    consecutive_fast_failures = 0
    consecutive_403_failures = 0
    FAST_FAIL_THRESHOLD_SECONDS = 20
    MAX_RETRY_DELAY_SECONDS = 120
    MAX_403_BEFORE_SKIP = 3

    while True:
        playlist = get_m3u_playlist(M3U_URL)
        if not playlist:
            print("⚠️ playlist boş, 10s bekle")
            time.sleep(10)
            continue

        if current_index >= len(playlist):
            current_index = 0
            last_seconds = 0
            last_url = ""
            last_title = ""

        item = playlist[current_index]
        target_url = item["url"]
        title = item["title"]

        if not is_valid_stream_url(target_url):
            print(f"⏭️ geçersiz URL, atla: {target_url[:100]}")
            current_index += 1
            last_seconds = 0
            last_url = ""
            last_title = ""
            update_local_state(current_index, 0, "", "")
            continue

        if last_seconds > 0 and last_url and target_url != last_url:
            if last_title and title == last_title:
                old = last_seconds
                last_seconds = max(0, last_seconds - LINK_CHANGE_REWIND_SECONDS)
                print(f"🔄 Link yenilendi: {old}s → {last_seconds}s")
            else:
                last_seconds = 0

        last_url = target_url
        last_title = title
        write_title_file(title)

        referer = guess_referer(target_url)
        headers_arg = build_headers_arg(referer)
        probe_url = probe_url_of(target_url)
        proxy = get_proxy()

        print("=" * 60)
        print(f"🎬 {title}")
        print(f"🔗 {target_url[:110]}")
        print(f"🌐 Proxy: {proxy or 'YOK'} | Referer: {referer}")

        total_dur = _duration_cache.pop(probe_url, 0.0)
        if total_dur <= 0:
            total_dur = get_video_duration_ffprobe(probe_url)

        input_options = [
            '-re',
            '-headers', headers_arg,
            '-protocol_whitelist', 'file,http,https,tcp,tls,crypto,hls,applehttp',
            '-allowed_extensions', 'ALL',
            '-err_detect', 'ignore_err',
            '-fflags', '+genpts+discardcorrupt',
            '-thread_queue_size', '1024',
            '-max_interleave_delta', '0',
            '-analyzeduration', '5000000',
            '-probesize', '5000000',
            '-reconnect', '1',
            '-reconnect_at_eof', '1',
            '-reconnect_streamed', '1',
            '-reconnect_delay_max', '10',
            '-rw_timeout', '5000000',
            '-threads', DECODER_THREADS,
        ]

        # ✅ Proxy varsa ekle
        if proxy:
            input_options += ['-http_proxy', proxy]

        seek_args = ['-ss', str(last_seconds)] if last_seconds > 0 else []

        if ";" in target_url:
            v_url, a_url = target_url.split(";", 1)
            input_args = input_options + seek_args + ['-i', v_url.strip()] + input_options + seek_args + ['-i', a_url.strip()]
            audio_map = ['-map', '1:a:0?']
            logo_idx = 2
        else:
            input_args = input_options + seek_args + ['-i', target_url]
            audio_map = ['-map', '0:a:0?']
            logo_idx = 1

        print_dashboard(title, current_index, len(playlist), last_seconds, status="🟡 Başlatılıyor")

        has_logo = os.path.exists('logo.png') and os.path.getsize('logo.png') > 0
        title_dt = f"drawtext=textfile='title.txt':reload=1:fontfile='{BOLD_FONT_PATH}':fontcolor=white@{TEXT_OPACITY}:fontsize=26:x=70:y=h-th-70"

        if has_logo:
            logo_inputs = ['-i', 'logo.png']
            filter_str = (
                '[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=25[main];'
                f'[{logo_idx}:v]scale=-2:83,format=rgba,colorchannelmixer=aa={LOGO_OPACITY}[logo1];'
                '[main][logo1]overlay=W-w-70:70[tmp1];'
                f'[tmp1]{title_dt}[v]'
            )
        else:
            logo_inputs = []
            filter_str = (
                '[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=25[main];'
                f'[main]{title_dt}[v]'
            )

        publisher.ensure_running()
        ts_offset = publisher.clock() + 1.0

        command = ['ffmpeg'] + input_args + logo_inputs + [
            '-filter_complex', filter_str, '-map', '[v]'
        ] + audio_map + [
            '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
            '-r', '25', '-b:v', '2500k', '-maxrate', '2500k', '-bufsize', '3000k',
            '-g', '50', '-c:a', 'aac', '-b:a', '128k', '-ac', '2', '-ar', '44100',
            '-output_ts_offset', f'{ts_offset:.3f}', '-flush_packets', '1',
            '-f', 'mpegts', 'pipe:1'
        ]

        process = subprocess.Popen(command, stdout=publisher.proc.stdin,
                                    stderr=subprocess.PIPE, universal_newlines=True)

        # Sonraki video süresini prefetch (proxy ile)
        if len(playlist) > 1:
            nxt = playlist[(current_index + 1) % len(playlist)]
            threading.Thread(target=prefetch_duration, args=(nxt["url"],), daemon=True).start()

        last_save = time.time()
        last_dash = time.time()
        current_sec = last_seconds
        stderr_tail = deque(maxlen=40)
        progress_ref = [time.time()]
        started_ref = [False]

        def _wd(proc=process, p=progress_ref, s=started_ref):
            while proc.poll() is None:
                time.sleep(5)
                to = WATCHDOG_TIMEOUT_SECONDS if s[0] else WATCHDOG_STARTUP_SECONDS
                if time.time() - p[0] > to:
                    print(f"🚨 Watchdog: {to}s veri yok, kill")
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    break

        threading.Thread(target=_wd, daemon=True).start()

        while True:
            line = process.stderr.readline()
            if not line and process.poll() is not None:
                break
            if line:
                stderr_tail.append(line.rstrip())
                low = line.lower()
                if any(x in low for x in ['error', 'failed', 'forbidden', '404', '403', 'invalid', 'no such']):
                    print(f"🐞 {line.rstrip()[:180]}")

            if "time=" in line:
                m = re.search(r'time=(\d+):(\d+):(\d+\.\d+)', line)
                if m:
                    h, mn, sc = m.groups()
                    played = int(h) * 3600 + int(mn) * 60 + float(sc)
                    current_sec = last_seconds + played
                    now = time.time()
                    progress_ref[0] = now
                    started_ref[0] = True
                    if now - last_save > 30:
                        update_local_state(current_index, current_sec, target_url, title)
                        last_save = now
                    if now - last_dash > 30:
                        print_dashboard(title, current_index, len(playlist), current_sec)
                        write_step_summary(title, current_index, len(playlist), current_sec)
                        last_dash = now

        if process.returncode != 0 and stderr_tail:
            print(f"🐞 çıkış kodu: {process.returncode}")
            for ln in list(stderr_tail)[-6:]:
                print(f"   {ln[:180]}")

        finished = (process.returncode == 0) and is_really_finished(current_sec, total_dur)

        # ✅ 403: proxy değiştir
        if not finished and is_forbidden_error(stderr_tail):
            consecutive_403_failures += 1
            print(f"🚫 403 ({consecutive_403_failures}/{MAX_403_BEFORE_SKIP})")
            if proxy:
                print(f"🔄 Proxy çalışmıyor olabilir: {proxy}. Yeni proxy aranacak...")
                _working_proxy["url"] = None
                _working_proxy["verified"] = False
                new_proxy = find_working_proxy(probe_url, referer)
                if new_proxy:
                    print(f"✅ Yeni proxy: {new_proxy}")
                    continue  # yeni proxy ile aynı içeriği tekrar dene
            if consecutive_403_failures >= MAX_403_BEFORE_SKIP:
                print(f"⏭️ {MAX_403_BEFORE_SKIP} kez 403, sonraki içeriğe geç")
                current_index += 1
                last_seconds = 0
                last_url = ""
                last_title = ""
                consecutive_403_failures = 0
                consecutive_fast_failures = 0
                update_local_state(current_index, 0, "", "")
            else:
                time.sleep(3)
            continue

        if finished:
            print("✅ Bitti, sonraki video")
            write_step_summary(title, current_index, len(playlist), current_sec, "✅ Bitti")
            current_index += 1
            last_seconds = 0
            last_url = ""
            last_title = ""
            update_local_state(current_index, 0, "", "")
            consecutive_fast_failures = 0
            consecutive_403_failures = 0
        else:
            if process.returncode == -9:
                print("⚠️ Watchdog kill")
            write_step_summary(title, current_index, len(playlist), current_sec, "🔴 Kopma")
            dur = current_sec - last_seconds
            if dur < FAST_FAIL_THRESHOLD_SECONDS:
                consecutive_fast_failures += 1
            else:
                consecutive_fast_failures = 0

            if consecutive_fast_failures >= 3:
                print("❌ sürekli hata, sonraki içerik")
                current_index += 1
                last_seconds = 0
                last_url = ""
                last_title = ""
                consecutive_fast_failures = 0
                update_local_state(current_index, 0, "", "")
            else:
                if consecutive_fast_failures > 1 and current_sec > SEEK_BACKOFF_SECONDS:
                    last_seconds = max(0, current_sec - SEEK_BACKOFF_SECONDS)
                    print(f"⏪ {SEEK_BACKOFF_SECONDS}s geri: {last_seconds}s")
                else:
                    last_seconds = current_sec
                last_url = target_url
                update_local_state(current_index, last_seconds, last_url, title)

        delay = 0 if finished else (min(5 * (2 ** consecutive_fast_failures), MAX_RETRY_DELAY_SECONDS) if consecutive_fast_failures > 0 else 5)
        if delay > 0:
            print(f"⚠️ {delay}s bekle")
            time.sleep(delay)


if __name__ == "__main__":
    try:
        start_m3u_stream()
    except KeyboardInterrupt:
        print("\n🛑 Durduruluyor")
    finally:
        publisher.stop()
