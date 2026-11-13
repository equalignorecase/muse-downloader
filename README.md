# Music Score Downloader — Python port

พอร์ต command-line ของส่วนขยายเบราว์เซอร์
[musescore-downloader](https://github.com/ingui-n/musescore-downloader)
มาเป็น Python. ดาวน์โหลดจากหน้า score ของ musescore.com:

- โน้ตเพลงเป็น **PDF**
- เสียงเป็น **MP3**
- **MIDI**

WebExtension เดิม (manifest v2/v3, React popup, content/background scripts)
เก็บไว้ข้างโฟลเดอร์นี้เพื่ออ้างอิง. Package นี้เขียน pipeline การดาวน์โหลดใหม่
ด้วย Python ธรรมดา.

## การแมปจาก extension มาเป็น Python

| ไฟล์เดิม | ไฟล์ Python ที่แทน |
| --- | --- |
| `src/content/content.js` | `musescore_downloader/downloader.py` |
| `src/content/tokenAlgorithm.js` | `musescore_downloader/token.py` |
| `src/content/scrape.js` + `src/background/background.js` | `musescore_downloader/browser.py` (Playwright) |
| `src/modules/utils.js` (`fetchApiUrl`) | `musescore_downloader/api.py` |
| `src/modules/utils.js` (ตัวช่วยอื่น ๆ) | `musescore_downloader/utils.py` |
| `pdfmake` สำหรับสร้าง PDF | `musescore_downloader/pdf.py` (ReportLab) |
| React popup | `musescore_downloader/cli.py` |
| browser cookie session | `--cookie`, `--cookie-file`, `--cookies-from-browser` |

Extension อ่าน metadata ของ score จาก DOM. พอร์ต Python อ่าน HTML ชุดเดียวกัน
(`ld+json`, `og:*` meta tags, ลิงก์ `score_0.svg/png` และ regex นับจำนวนหน้า)
ด้วย standard library.

## ติดตั้ง

```bash
cd musescore-downloader-py
python3 -m venv .venv
source .venv/bin/activate

# core + PDF support
pip install -e '.[pdf]'

# recommended: browser mode (Cloudflare token capture) + browser cookies
pip install -e '.[pdf,browser,browsers]'
playwright install chromium
```

`curl-cffi` จะถูกติดตั้งอัตโนมัติพร้อม extra `browser`. ตัวนี้ให้ TLS
fingerprint แบบ Chrome จริง ทำให้ CLI ผ่าน Cloudflare สำหรับ request ไปยัง API
และ CDN โดยใช้ cookies จากเบราว์เซอร์. เป็น dependency แบบ optional: ถ้าไม่มี
โปรแกรมจะ fallback ไปใช้ `requests` ธรรมดาและ Playwright.

ถ้าไม่ต้องการติดตั้งเป็น package:

```bash
pip install -r requirements.txt
python -m musescore_downloader --help
```

> **Tip:** musescore.com อยู่หลัง Cloudflare ดังนั้น `requests` ธรรมดามักถูก
> ตอบด้วย challenge. เมื่อติดตั้ง `curl-cffi` แล้ว โหมดปกติจะใช้ได้กับ score
> สาธารณะส่วนใหญ่. ใช้ `--browser` เมื่อเว็บยัง challenge request หรือเมื่อ
> score ต้องใช้ session ที่ล็อกอินอยู่.

## ใช้งาน

### วิธีง่ายสุด: `main.py`

```bash
python main.py
```

จะแสดงเมนูสั้น ๆ (info / PDF / MP3 / MIDI / everything) ถาม URL หรือ ID ของ
score แล้วสร้างคำสั่ง CLI ให้. ถ้าใส่ argument จะข้ามเมนูและทำงานเหมือนคำสั่ง
`musescore-downloader` ทุกประการ:

```bash
python main.py info 123456 --browser
python main.py sheet 123456 --browser -o ~/Music/sheets/
```

### CLI

```bash
# score metadata
musescore-downloader info 123456 --browser

# PDF sheet music
musescore-downloader sheet https://musescore.com/user/1/scores/123456 --browser
musescore-downloader sheet 123456 --browser -o ~/Music/sheets/

# MP3 / MIDI
musescore-downloader audio 123456 --browser
musescore-downloader midi 123456 --browser

# everything into a folder
musescore-downloader all 123456 --browser -o ~/Music/sheets/
```

รับได้ทั้ง numeric ID, relative path และ full URL.

ตัวเลือกที่ใช้บ่อย:

```text
--browser                    โหลดหน้าใน Chromium (แนะนำ)
--no-headless                แสดงหน้าต่าง Chromium
--cookie 'a=b; c=d'          Cookie header แบบ raw
--cookie-file cookies.txt    Netscape cookies.txt, JSON export หรือ raw header
--cookies-from-browser chrome
--pages N                    ระบุจำนวนหน้าเอง แทนค่าที่ตรวจได้
--allow-partial              บันทึก PDF แม้บางหน้าดาวน์โหลดไม่สำเร็จ
--scrape-wait 15             วินาทีที่รอเพื่อดัก token
--user-agent '...'           ตั้ง User-Agent ให้ตรงกับเบราว์เซอร์ที่ใช้ cookies
-v / -q                      verbose / quiet
```

สำหรับ `-o` ถ้าลงท้ายด้วย `/` (หรือเป็นโฟลเดอร์ที่มีอยู่แล้ว) จะหมายถึง
"วางไฟล์ในโฟลเดอร์นี้"; ถ้าไม่ใช่ จะถือว่าเป็นชื่อไฟล์.

### การยืนยันตัวตน

Score สาธารณะปกติใช้ได้โดยไม่ต้องมี cookies. ถ้าเว็บขอให้ล็อกอิน หรือ score
ไม่ได้เป็นสาธารณะ ให้ส่ง cookies จาก session ที่ล็อกอินอยู่:

```bash
# easiest: read cookies directly from a local browser
musescore-downloader sheet 123456 --cookies-from-browser firefox

# or export cookies.txt with a browser extension
musescore-downloader audio 123456 --cookie-file ~/cookies.txt

# or pass the Cookie header from DevTools
musescore-downloader midi 123456 --cookie 'musescore_session=...'
```

## หลักการทำงาน

1. โหลดหน้า score และดึง metadata (ชื่อเพลง, composer, id, จำนวนหน้า,
   ภาพหน้าแรก).
2. ลองใช้ token algorithm:
   - หา URL preload ของ `/static/public/build/.../*.js`;
   - สแกน script หา MD5 initialisation constants แล้วดึง secret ด้วย regex
     เดียวกับ extension;
   - token = `md5(scoreId + type + index + secret)[:4]`;
   - เรียก `https://musescore.com/api/jmuse?id=...&index=...&type=...`.
3. `curl-cffi` ใช้ cookies เดิมพร้อม Chrome TLS fingerprint สำหรับ `api/jmuse`,
   ไฟล์ static และดาวน์โหลดจาก CDN; ถ้าล้มเหลว client จะ fallback ไปใช้
   in-page Playwright `fetch` หรือ request context ของ Playwright.
4. ถ้า token algorithm ใช้ไม่ได้ (MuseScore เปลี่ยน build) จะ fallback ไปใช้
   Playwright: โหลดหน้า, ขยาย score container, กดปุ่ม play/MIDI แล้วดัก
   `Authorization` header จริงจาก request `api/jmuse`.
5. ดาวน์โหลดทุกหน้า (SVG หรือ PNG) แล้วสร้าง PDF ขนาด A4 ด้วย ReportLab;
   ดาวน์โหลด MP3/MIDI ด้วย session ที่ยืนยันตัวตนแล้ว.

Secret ที่ดึงได้จะถูก cache ไว้ที่
`~/Library/Caches/musescore-downloader/token_secret.json` (macOS) หรือ
`~/.cache/musescore-downloader/token_secret.json` (Linux) โดย key ตาม build URL.

## ใช้เป็น library

```python
from musescore_downloader.downloader import MuseScoreDownloader
from musescore_downloader.utils import ConsoleLogger

downloader = MuseScoreDownloader(
    "https://musescore.com/user/1/scores/123456",
    logger=ConsoleLogger(),
)

print(downloader.load().to_dict())
downloader.download_sheet("out/sheet.pdf")
downloader.download_audio("out/audio.mp3")
downloader.download_midi("out/song.mid")
```

## Tests

```bash
python -m unittest discover -s tests -v
```

## หมายเหตุและข้อจำกัด

- Official score ดาวน์โหลดไม่ได้ เหมือนกับ extension เดิม.
- จำนวนหน้าอ่านจาก escaped score metadata (หรือ DOM ที่ render แล้วใน browser
  mode). ถ้า score ซ่อนจำนวนหน้าไว้ ให้ใช้ `--pages N` หรือปล่อยให้โปรแกรม
  probe index ของหน้า (`--max-pages`).
- MuseScore เปลี่ยน front end เป็นประจำ. ถ้า token generation หยุดทำงาน ให้ใช้
  `--browser` เพื่อดัก token จริงจาก network.
- Cloudflare อาจยัง challenge การเรียก API อัตโนมัติ. Cookies จาก session ที่
  ล็อกอินแล้ว (`--cookies-from-browser chrome`) เป็นวิธีที่เชื่อถือได้ที่สุด
  และ `--no-headless` ช่วยได้. ถ้าดาวน์โหลดได้เพียงบางหน้า โปรแกรมจะหยุดพร้อม
  error แทนการเขียน PDF ที่ไม่สมบูรณ์; เพิ่ม `--allow-partial` เพื่อบันทึก
  เท่าที่ดาวน์โหลดได้.
- โปรเจกต์นี้เป็นพอร์ตของเครื่องมือที่ใช้สัญญาอนุญาต MIT และตั้งใจให้ใช้เพื่อ
  การศึกษา/ใช้งานส่วนตัวเท่านั้น. โปรดเคารพข้อกำหนดและลิขสิทธิ์ของ
  musescore.com.

## License

MIT ตามโปรเจกต์เดิม (`LICENSE`).
