# ethanplusai/jarvis → Windows Fork Yol Haritası

Orijinal repo macOS'a sıkı bağlı: takvim, mail ve not erişiminin tamamı AppleScript köprüsüyle yapılıyor. Bu belge hangi parçanın olduğu gibi kalacağını, hangisinin neyle değiştirileceğini ve hangi sırayla ilerleneceğini anlatıyor.

## Aşama 0 — Klon ve envanter (yarım gün)

```powershell
git clone https://github.com/ethanplusai/jarvis.git
cd jarvis
```

Dosyaları iki kümeye ayır. Platform bağımsız olanlar (dokunma): `server.py` çekirdeği (FastAPI + WebSocket akışı), `memory.py` (SQLite hafıza), `tracking.py` (başarı metrikleri), frontend. macOS'a bağlı olanlar (değişecek): `calendar_access.py` ve AppleScript çağrısı yapan tüm modüller (`osascript` geçen her yer), Mail/Notes erişimi, terminal spawn mantığı.

Hızlı tarama: `findstr /s /i "osascript applescript" *.py` — çıkan her dosya değişim listesine girer.

## Aşama 1 — Önce ayağa kaldır, sonra değiştir (1 gün)

macOS modüllerini silmeden önce her birinin yerine sahte (mock) sürüm koy: takvim fonksiyonu boş liste döndürsün, mail okuma "0 okunmamış" desin. Amaç, çekirdek ses döngüsünün (mikrofon → Claude → TTS) Windows'ta çalıştığını önce kanıtlamak. SSL adımı localhost'ta gereksiz; `ws://localhost` kullanarak sertifika üretimini tamamen atlayabilirsin.

## Aşama 2 — TTS değişimi: Fish Audio → edge-tts (yarım gün)

Fish Audio ücretli ve Türkçe desteği sınırlı. `edge-tts` ücretsiz, Türkçe sesleri kaliteli (`tr-TR-AhmetNeural`, `tr-TR-EmelNeural`). Fish Audio'ya istek atan fonksiyonu bul, imzasını koru, içini edge-tts ile değiştir — böylece çağıran kod hiç değişmez. (Hazır örnek: jarvis-tr iskeletindeki `seslendir()` fonksiyonu birebir kopyalanabilir.)

Bu adım `.env`'den `FISH_API_KEY` zorunluluğunu da kaldırır → tek maliyet Anthropic API kalır.

## Aşama 3 — Apple servislerinin Windows muadilleri (2-4 gün)

| macOS modülü | Windows muadili | Not |
|---|---|---|
| Apple Calendar (AppleScript) | **Google Calendar API** | OAuth kurulumu bir kerelik; `google-api-python-client` ile okuma 30 satır |
| Apple Mail (read-only) | **Gmail API** (readonly scope) | Orijinal de salt-okunur, aynı felsefeyi koru |
| Apple Notes | **Yerel markdown dosyaları** | En basit ve taşınabilir çözüm; sonra Notion API'ye yükseltilebilir |
| Terminal / Claude Code spawn | **`subprocess` + PowerShell** | Claude Code Windows'ta çalışıyor; `claude -p "görev"` komutuyla başlatılabilir |

Her modülü tek tek değiştir, her değişimden sonra sesle uçtan uca test et ("yarın takvimimde ne var?" gibi).

## Aşama 4 — Türkçeleştirme (1 gün)

- `server.py` içindeki sistem promptunu Türkçe yaz (JARVIS karakteri korunabilir ama dil Türkçe olsun).
- Frontend'de Web Speech API dilini `tr-TR` yap.
- Eylem etiketlerinin tetikleyici örneklerini Türkçe komutlarla güncelle ("notuma yaz", "takvime bak").

## Aşama 5 — Sağlamlaştırma (sürekli)

- Hafıza ve görev sistemi (`memory.py`) zaten SQLite — olduğu gibi çalışır, sadece Türkçe içerikle test et.
- `tracking.py` başarı metriklerini tutuyor; hangi komutların başarısız olduğunu buradan izleyip promptu iyileştir.
- Windows başlangıcında otomatik çalıştırma: Görev Zamanlayıcı'ya `pythonw server.py` ekle.

## Tahmini toplam süre

Yoğun çalışmayla **1 hafta** içinde mock'suz, tam işlevli bir Windows sürümü mümkün. En riskli adım Google OAuth kurulumu (Aşama 3) — ilk kez yapıyorsan yarım gün ayır.

## Lisans notu

Orijinal repo MCU'dan esinlenme konusunda açık bir feragatname taşıyor; fork'unda da bu feragatnameyi koru. Ticari bir ürüne dönüştüreceksen "JARVIS" adını kullanma — Marvel'ın markası. Kendi ürünün için farklı bir isim seç (jarvis-tr bile sadece kişisel kullanım için güvenli sayılır).
