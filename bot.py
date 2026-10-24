import subprocess
import sys
import time

# ===================== SSH101.com AYARLARI =====================
RTMP_URL = "rtmp://ssh101.bozztv.com:1935/ssh101"
STREAM_KEY = "digimax2"
rtmp_server = f"{RTMP_URL}/{STREAM_KEY}"

# ===================== YAYIN AYARLARI =====================
VIDEO_URL = "http://germanyservers.life:80/live/U1T3E7Vr/g2ktb9mu/178661.m3u8"
LOGO_URL = "https://raw.githubusercontent.com/gistmedya/0101/refs/heads/main/1792821507087.png"

print("=" * 50)
print("📺 SSH101.com Yayın Başlatılıyor")
print("=" * 50)
print(f"🎬 Video: {VIDEO_URL}")
print(f"🎨 Logo: {LOGO_URL}")
print(f"🔑 Stream Key: {STREAM_KEY}")
print(f"📡 RTMP: {rtmp_server}")
print(f"🌐 İzleme: https://ssh101.com/live/{STREAM_KEY}")
print(f"📱 HLS: https://lbgo.bozztv.com/ssh101/ssh101/{STREAM_KEY}/playlist.m3u8")
print("=" * 50)

# FFmpeg komutu - 1080p 25 FPS & 3500 kbps Bitrate (KMPS)
command = [
    'ffmpeg',
    '-user_agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    '-rw_timeout', '15000000',
    '-thread_queue_size', '1024',
    '-re',
    '-stream_loop', '-1',
    '-i', VIDEO_URL,
    '-thread_queue_size', '1024',
    '-i', LOGO_URL,
    '-filter_complex',
    '[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black[v0];'
    '[1:v]scale=-1:99[logo];'
    '[v0][logo]overlay=W-w-38:25[v1];'
    '[v1]drawtext=text=:fontcolor=white:fontsize=28:box=1:boxcolor=black@0.6:boxborderw=5:x=(w-text_w)/2:y=h-text_h-20[v]',
    '-map', '[v]',
    '-map', '0:a?',
    '-c:v', 'libx264',
    '-r', '25',
    '-preset', 'veryfast',
    '-b:v', '3500k',
    '-maxrate', '3500k',
    '-bufsize', '7000k',
    '-c:a', 'aac',
    '-b:a', '128k',
    '-f', 'flv',
    rtmp_server
]

print("\n🎥 SSH101.com yayını başlatılıyor...")
print("📐 Çözünürlük: 1080p (1920x1080) @ 25 FPS")
print("⚡ Bitrate (KMPS): 3500 kbps")
print("🖼️  Logo: Sağ üst")
print("📝 Alt yazı: t.me/digitaltivi")
print("⏸️  Durdurmak için: Ctrl + C\n")

try:
    proc = subprocess.Popen(command)
    
    # Yayını canlı tut
    while True:
        time.sleep(60)
        if proc.poll() is not None:
            print("⚠️ Yayın durdu, yeniden başlatılıyor...")
            proc = subprocess.Popen(command)
            
except KeyboardInterrupt:
    print("\n\n⛔ Yayın durduruluyor...")
    proc.terminate()
    print("✅ Yayın sonlandırıldı.")
