"""
JARVIS-TR — Windows için Türkçe sesli asistan iskeleti
Mimari: Tarayıcı (Web Speech API, tr-TR) → WebSocket → FastAPI → Claude → edge-tts → Tarayıcı

Çalıştırma:  python server.py  →  http://localhost:8000 (Chrome veya Edge)
"""

import asyncio
import base64
import json
import os
import re
import webbrowser
from datetime import datetime
from pathlib import Path

import edge_tts
import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# ----------------------------------------------------------------- ayarlar

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MODEL = os.getenv("MODEL", "gemini-2.5-flash")
TTS_VOICE = os.getenv("TTS_VOICE", "tr-TR-EmelNeural")  # erkek ses: tr-TR-AhmetNeural
TTS_RATE = os.getenv("TTS_RATE", "+8%")  # konuşma hızı, daha akıcı olması için varsayılan biraz hızlandırılmış
USER_NAME = os.getenv("USER_NAME", "Efendim")
MAX_HISTORY = 24  # hafızada tutulacak mesaj sayısı (12 tur)

ROOT = Path(__file__).parent
NOTLAR = ROOT / "notlar.md"

SISTEM_PROMPTU = f"""Sen JARVIS-TR'sin — {USER_NAME} için çalışan Türkçe sesli bir yapay zeka asistanı.

Kurallar:
- Cevapların SESLİ OKUNACAK. Kısa, doğal ve konuşma diliyle yaz. Madde işareti, başlık, markdown KULLANMA.
- Genelde 1-3 cümle yeterli. Kullanıcı detay isterse uzat.
- Kibar ama samimi ol. Gereksiz "Tabii ki! Elbette!" girişleri yapma, doğrudan cevapla.
- Bugünün tarihi ve saati sana her mesajda iletilir, ona güven.

Eylem etiketleri — gerektiğinde cevabının SONUNA ekle (tek satır, en fazla bir etiket):
- [EYLEM:AC] hedef → bilgisayarda program veya web sitesi açar.
  Örnek: "Notepad'i açıyorum. [EYLEM:AC] notepad" veya "[EYLEM:AC] https://youtube.com"
- [EYLEM:NOT] metin → notu yerel not dosyasına kaydeder.
  Örnek: "Notunu aldım. [EYLEM:NOT] Yarın saat 14:00 müşteri görüşmesi"

Etiket kullandığında bunu konuşma metninde doğal biçimde belirt ("açıyorum", "kaydettim" gibi),
ama etiketin kendisini asla sesli metnin içine karıştırma — her zaman en sonda dursun."""

app = FastAPI(title="JARVIS-TR")

# ----------------------------------------------------------------- yardımcılar

EYLEM_DESENI = re.compile(r"\[EYLEM:(AC|NOT)\]\s*(.+?)\s*$", re.MULTILINE)


def eylem_calistir(tur: str, hedef: str) -> str:
    """Modelin döndürdüğü eylem etiketini Windows üzerinde çalıştırır."""
    try:
        if tur == "AC":
            if hedef.startswith(("http://", "https://")):
                webbrowser.open(hedef)
            else:
                # Windows: programı veya dosyayı varsayılan şekilde başlat
                os.startfile(hedef)  # type: ignore[attr-defined]
            return f"AÇILDI: {hedef}"
        if tur == "NOT":
            zaman = datetime.now().strftime("%d.%m.%Y %H:%M")
            with open(NOTLAR, "a", encoding="utf-8") as f:
                f.write(f"- **{zaman}** — {hedef}\n")
            return "NOT KAYDEDİLDİ"
    except Exception as hata:  # noqa: BLE001
        return f"EYLEM HATASI: {hata}"
    return "BİLİNMEYEN EYLEM"


async def claude_sor(gecmis: list[dict]) -> str:
    """Gemini API'ye konuşma geçmişini gönderir, metin cevabını döndürür."""
    icerikler = [
        {
            "role": "model" if mesaj["role"] == "assistant" else "user",
            "parts": [{"text": mesaj["content"]}],
        }
        for mesaj in gecmis
    ]
    async with httpx.AsyncClient(timeout=60) as istemci:
        yanit = await istemci.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
            params={"key": GEMINI_API_KEY},
            headers={"content-type": "application/json"},
            json={
                "system_instruction": {"parts": [{"text": SISTEM_PROMPTU}]},
                "contents": icerikler,
            },
        )
    yanit.raise_for_status()
    veri = yanit.json()
    parcalar = veri["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parcalar)


async def sesi_metne_cevir(ses_b64: str, mime: str) -> str:
    """Mikrofon kaydını Gemini ile Türkçe metne çevirir."""
    async with httpx.AsyncClient(timeout=60) as istemci:
        yanit = await istemci.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
            params={"key": GEMINI_API_KEY},
            headers={"content-type": "application/json"},
            json={
                "contents": [{
                    "role": "user",
                    "parts": [
                        {"inline_data": {"mime_type": mime, "data": ses_b64}},
                        {"text": "Bu ses kaydındaki Türkçe konuşmayı sadece düz metne çevir. "
                                  "Başka hiçbir açıklama veya yorum ekleme, sadece söylenen sözü yaz."},
                    ],
                }],
            },
        )
    yanit.raise_for_status()
    veri = yanit.json()
    parcalar = veri["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parcalar).strip()


async def seslendir(metin: str) -> str:
    """edge-tts ile Türkçe ses üretir, base64 mp3 döndürür."""
    iletisim = edge_tts.Communicate(metin, TTS_VOICE, rate=TTS_RATE)
    parcalar = []
    async for parca in iletisim.stream():
        if parca["type"] == "audio":
            parcalar.append(parca["data"])
    return base64.b64encode(b"".join(parcalar)).decode()


# ----------------------------------------------------------------- websocket

@app.websocket("/ws")
async def ws_baglanti(ws: WebSocket):
    await ws.accept()
    gecmis: list[dict] = []

    try:
        while True:
            ham = await ws.receive_text()
            mesaj = json.loads(ham)

            if mesaj.get("audio"):
                await ws.send_json({"type": "durum", "value": "dusunuyor"})
                try:
                    kullanici_metni = await sesi_metne_cevir(mesaj["audio"], mesaj.get("mime", "audio/webm"))
                except Exception as hata:  # noqa: BLE001
                    await ws.send_json({"type": "hata", "text": f"Ses tanıma hatası: {hata}"})
                    continue
                if kullanici_metni:
                    await ws.send_json({"type": "tanima", "text": kullanici_metni})
            else:
                kullanici_metni = (mesaj.get("text") or "").strip()

            if not kullanici_metni:
                continue

            simdi = datetime.now().strftime("%d.%m.%Y %A %H:%M")
            gecmis.append({"role": "user", "content": f"[{simdi}] {kullanici_metni}"})
            gecmis[:] = gecmis[-MAX_HISTORY:]

            await ws.send_json({"type": "durum", "value": "dusunuyor"})

            try:
                cevap = await claude_sor(gecmis)
            except Exception as hata:  # noqa: BLE001
                await ws.send_json({"type": "hata", "text": f"Claude API hatası: {hata}"})
                gecmis.pop()
                continue

            gecmis.append({"role": "assistant", "content": cevap})

            # Eylem etiketlerini ayıkla ve çalıştır
            eylem_sonucu = None
            es = EYLEM_DESENI.search(cevap)
            if es:
                eylem_sonucu = eylem_calistir(es.group(1), es.group(2))
            konusma_metni = EYLEM_DESENI.sub("", cevap).strip()

            # Ses üret (başarısız olursa sadece metinle devam et)
            ses_b64 = ""
            try:
                if konusma_metni:
                    ses_b64 = await seslendir(konusma_metni)
            except Exception:  # noqa: BLE001
                pass

            await ws.send_json({
                "type": "cevap",
                "text": konusma_metni,
                "audio": ses_b64,
                "eylem": eylem_sonucu,
            })

    except WebSocketDisconnect:
        pass


# ----------------------------------------------------------------- statik

@app.get("/")
async def ana_sayfa():
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

# ----------------------------------------------------------------- başlat

if __name__ == "__main__":
    if not GEMINI_API_KEY:
        print("UYARI: .env dosyasında GEMINI_API_KEY tanımlı değil!")
    print(f"JARVIS-TR hazır → http://localhost:8000  (ses: {TTS_VOICE}, model: {MODEL})")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
