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

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = os.getenv("MODEL", "claude-haiku-4-5-20251001")
TTS_VOICE = os.getenv("TTS_VOICE", "tr-TR-AhmetNeural")  # kadın ses: tr-TR-EmelNeural
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
    """Anthropic API'ye konuşma geçmişini gönderir, metin cevabını döndürür."""
    async with httpx.AsyncClient(timeout=60) as istemci:
        yanit = await istemci.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": MODEL,
                "max_tokens": 1024,
                "system": SISTEM_PROMPTU,
                "messages": gecmis,
            },
        )
    yanit.raise_for_status()
    veri = yanit.json()
    return "".join(b.get("text", "") for b in veri.get("content", []) if b.get("type") == "text")


async def seslendir(metin: str) -> str:
    """edge-tts ile Türkçe ses üretir, base64 mp3 döndürür."""
    iletisim = edge_tts.Communicate(metin, TTS_VOICE)
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
    if not ANTHROPIC_API_KEY:
        print("UYARI: .env dosyasında ANTHROPIC_API_KEY tanımlı değil!")
    print(f"JARVIS-TR hazır → http://localhost:8000  (ses: {TTS_VOICE}, model: {MODEL})")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
