# gaming

[![CI](https://github.com/devprogrmer/gaming/actions/workflows/ci.yml/badge.svg)](https://github.com/devprogrmer/gaming/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

> **توجه:** «gaming» فقط نامِ پروژه است. این ابزار **یک بازی، موتور بازی یا لانچر نیست**.
> بلکه یک **ابزار خط‌فرمان برای کشف رِنج‌های IP و بررسی سلامت و دسترس‌پذیری شبکه** است.

---

## معرفی پروژه

`gaming` یک ابزار خط‌فرمان (CLI) است که:

- رِنج‌های IP را از منابع عمومی شبکه کشف می‌کند (RDAP، WHOIS، ASN/BGP، PeeringDB، تخصیص‌های RIR).
- این رِنج‌ها را فیلتر و نرمال‌سازی می‌کند (اعتبارسنجی، حذف موارد تکراری، ادغام و در صورت نیاز فشرده‌سازی CIDR).
- دسترس‌پذیری و سلامت آن‌ها را می‌سنجد (تأخیر، درصد بسته‌های گم‌شده، بررسی پورت‌ها).
- نتیجه را به‌صورت گزارش‌های ساختاریافته در **کنسول، JSON یا CSV** خروجی می‌گیرد.

علاوه بر حالت خط‌فرمانِ کلاسیک، این پروژه یک **حالت تعاملی و منویی (Interactive)** هم دارد
که مخصوص کاربران عادی طراحی شده است: کافی است برنامه را اجرا کنید و از داخل منو، اسکن
رِنج‌های داخلی (ایران) یا خارجی را شروع کنید و نتیجه را به‌صورت ساده‌ی **GOOD / MEDIUM / BAD**
ببینید — بدون نیاز به هیچ دستور پیچیده‌ای.

### ویژگی‌های کلیدی

- **حالت تعاملی و منویی** برای بررسی سلامت IP به‌صورت زنده (`gaming menu`).
- **داشبورد وب محلی** (`gaming web`) برای جست‌وجو، اسکن، تاریخچه و تنظیمات — تماماً آفلاین و بدون وابستگی.
- **دسترس‌پذیری دوطرفه (ایران + خارج)**: هر میزبان هم از ایران و هم از خارج بررسی و به‌صورت INTERNATIONAL / IRAN_ONLY / ABROAD_ONLY / UNREACHABLE دسته‌بندی می‌شود.
- **دو سرویسِ بررسی از خارج**: check-host.net و (اختیاری) RIPE Atlas، با حالتِ ترکیبی `both`؛ «قطعیِ سرویس» از «در دسترس نبودن» جدا نمایش داده می‌شود.
- **دو گردش‌کارِ آماده**: رِنج‌های **ایران** و رِنج‌های **خارجی** با فهرست‌های CIDR داخلی و قابل‌ویرایش.
- **کشف IPهای زنده** با یک اسکن سریع، و امکان تبدیل آن به یک اسکنِ کاملِ سلامت.
- **اندازه‌گیری تأخیر و بسته‌های گم‌شده** به‌صورت چندسکویی (بدون نیاز به `fping`/`tail`/`watch`).
- **اسکن اختیاریِ پورت‌های رایج** و **اسکن‌های زمان‌بندی‌شده** با هشدارِ تغییر وضعیت (webhook اختیاری).
- **دسته‌بندی سادهٔ سلامت به سبک Check-Host**: GOOD / MEDIUM / BAD با آستانه‌های قابل‌تنظیم.
- **ذخیرهٔ دائمی تاریخچه** در یک پایگاه‌دادهٔ محلی SQLite که بین اجراها باقی می‌ماند.
- **اعتبارسنجی تازگیِ دادهٔ منابع** (`gaming validate-seed`) با نشانهٔ `last_validated`.
- **بدون وابستگی خارجی**: فقط **کتابخانهٔ استاندارد پایتون ۳٫۱۱+** — هر جا پایتون باشد اجرا می‌شود.
- **خرابی‌پذیری نرم (fail-soft)**: اگر یک منبع یا یک میزبان جواب ندهد، کل اجرا متوقف نمی‌شود.

---

## نصب سریع (Quick Install)

اگر فقط می‌خواهید ابزار را سریع راه بیندازید و وارد منوی تعاملی شوید:

**لینوکس / مک / Git Bash / WSL**

```bash
git clone https://github.com/devprogrmer/gaming.git
cd gaming
./install.sh          # برای ساخت لینک ~/.local/bin/gaming گزینهٔ --user را اضافه کنید
./gaming              # اجرای منوی تعاملی
```

**ویندوز (PowerShell)**

```powershell
git clone https://github.com/devprogrmer/gaming.git
cd gaming
powershell -ExecutionPolicy Bypass -File .\install.ps1
.\gaming.cmd
```

اسکریپت نصب به‌صورت خودکار یک **محیط مجازی (virtualenv)** می‌سازد، ابزار را نصب می‌کند و یک
اجراکنندهٔ `gaming` برایتان درست می‌کند. نیازی به نصب دستیِ هیچ وابستگی‌ای نیست.

> **نکته:** اسکریپت نصب را از داخل پوشهٔ خودِ مخزن (که فقط یک `gaming` قابل‌اجرا می‌سازد)
> اجرا کنید. اگر در همان پوشه از قبل یک **پوشه** یا فایل به نام `gaming` وجود داشته باشد
> (مثلاً از یک نصب ناقصِ قبلی یا جایی که `src/gaming/` کنارش استخراج شده)، ساختِ اجراکننده
> ممکن نیست و خطای `./gaming: Is a directory` می‌گیرید. در این حالت نصب‌کننده اکنون با پیام
> روشن متوقف می‌شود؛ آن پوشه را حذف کنید یا نصب را از یک پوشهٔ تمیز اجرا کنید. در پایانِ نصب،
> اسکریپت خودش اجراکننده را با `--version` می‌آزماید تا مطمئن شود سالم است.

---

## نصب کامل (گام‌به‌گام)

### پیش‌نیاز

- **پایتون نسخهٔ ۳٫۱۱ یا بالاتر** (به `tomllib` از کتابخانهٔ استاندارد نیاز دارد).
- بدون هیچ وابستگیِ خارجیِ زمان‌اجرا.

نسخهٔ پایتون خود را بررسی کنید:

```bash
python --version      # باید 3.11 یا بالاتر باشد
```

### روش ۱ — نصب با اسکریپت (پیشنهادی برای کاربران عادی)

همان روش «نصب سریع» بالا. اسکریپت `install.sh` یا `install.ps1` همه‌چیز را خودکار انجام می‌دهد.

### روش ۲ — نصب دستی با pip

نصب از روی سورس:

```bash
git clone https://github.com/devprogrmer/gaming.git
cd gaming
python -m pip install .
```

نصب در حالت توسعه/ویرایش‌پذیر (دستور `gaming` و ابزارهای توسعه را هم اضافه می‌کند):

```bash
python -m pip install -e ".[dev]"
```

اجرا بدون نصب:

```bash
PYTHONPATH=src python -m gaming --help
```

---

## نحوهٔ اجرا

### اجرای حالت تعاملی (منویی)

کافی است `gaming` را بدون هیچ آرگومانی اجرا کنید (یا `gaming menu`). منوی زیر باز می‌شود:

```
==================================================
   devprogrmer * IP Health Scanner   (v0.7.0)
==================================================
  1) Scan saved ranges (datacenter / CDN / both)
  2) Discover & save provider ranges
  3) Manage IP ranges
  4) View scan history
  5) Settings
  6) Update installed version
  7) Filter CIDRs by first octet
  8) Discover, save & scan a provider
  9) Launch web panel
  0) Exit
--------------------------------------------------
```

معنی گزینه‌های منو:

| گزینه | کار آن |
|---|---|
| **۱) Scan saved ranges** | اسکن سلامت روی رِنج‌های ذخیره‌شده (دیتاسنتر / CDN / هر دو، ایران / خارجی). |
| **۲) Discover & save provider ranges** | کشف رِنج‌های ارائه‌دهنده‌ها و ذخیرهٔ خودکار در دسته‌های مربوطه. |
| **۳) Manage IP ranges** | افزودن یا حذف رِنج‌های دلخواه (CIDR) به تفکیک دسته. |
| **۴) View scan history** | مرور اسکن‌های قبلی که در پایگاه‌دادهٔ محلی ذخیره شده‌اند. |
| **۵) Settings** | تنظیم آستانه‌ها، پینگ، هم‌زمانی، بررسیِ خارج، ارائه‌دهنده، اسکن پورت و هشدارها. |
| **۶) Update installed version** | به‌روزرسانی درجای نسخهٔ نصب‌شده. |
| **۷) Filter CIDRs by first octet** | کشف و فیلترِ پویا بر اساس اولین اکتت + دیتاسنتر. |
| **۸) Discover, save & scan a provider** | یک ارائه‌دهندهٔ مشخص: کشف ← ذخیره ← اسکن، یک‌جا. |
| **۹) Launch web panel** | اجرای همان داشبورد وب `gaming web` در همین پردازه (بدون subprocess)؛ گزینهٔ bind/port/tls را می‌پرسد و با Ctrl+C به‌صورت تمیز متوقف شده و به منو برمی‌گردد. |
| **۰) Exit** | خروج از برنامه. |

### اجرای حالت خط‌فرمان (برای کاربران حرفه‌ای)

```bash
# فهرست منابع کشف موجود
gaming sources

# کشف رِنج‌ها به‌صورت آفلاین (دادهٔ نمونهٔ داخلی)، تمرکز روی دیتاسنترهای ایران، خروجی JSON
gaming --offline discover --iran-datacenter --format json

# رِنج‌های خارجی، فشرده‌سازی پیشوندها، خروجی CSV
gaming --offline discover --foreign-datacenter --collapse --format csv -o foreign.csv

# بررسی دسترس‌پذیری چند پیشوند مشخص (بررسی زنده‌بودن محلی + پروب پورت)
gaming check 1.1.1.1 8.8.8.0/24 --ports 80,443 --format console

# خط‌لولهٔ کامل: کشف ← فیلتر ← نرمال‌سازی ← دسترس‌پذیری ← گزارش
gaming --offline run --country IR --ports 80,443 --format json -o report.json
```

> بررسی‌های سراسری (`--global`) و کشف در حالت غیرآفلاین به اینترنت عمومی وصل می‌شوند.
> فقط روی زیرساختی از آن‌ها استفاده کنید که مجاز به بررسی آن هستید.

---

### داشبورد وب (`gaming web`)

یک داشبورد وب محلی برای جست‌وجو، اسکن دوطرفه (ایران + خارج)، تاریخچه و تنظیمات.
تماماً با کتابخانهٔ استاندارد پایتون ساخته شده (بدون هیچ وابستگی جدید) و کاملاً آفلاین
کار می‌کند.

A local web dashboard for search, bidirectional (Iran + abroad) scanning,
history, and settings. Stdlib-only, no new dependencies, works fully offline.

```bash
# اجرا روی یک پورت آزاد تصادفی (۲۰۰۰۰–۶۵۰۰۰)؛ نام کاربری و رمز یک‌بار چاپ می‌شوند
gaming web

# محدود به لوکال‌هاست + پورت مشخص (امن‌ترین حالت روی سرور اشتراکی)
gaming web --bind 127.0.0.1 --port 8080

# سرویس‌دهی روی HTTPS با گواهی self-signed (کش‌شده در پوشهٔ دادهٔ برنامه)
gaming web --tls

# بازتولید نام کاربری/رمز و باطل‌کردن همهٔ نشست‌ها (مسیر بازیابی)
gaming web --reset-credentials
```

**Live Scan: اسکن همه‌باهم یا یکی‌یکی (scan mode):**

صفحهٔ **Live Scan** پیش از شروعِ اسکن یک انتخاب صریح می‌دهد:

- **Scan all together** (پیش‌فرض، رفتار قبلی) — یک Job واحد روی همهٔ CIDRهای منطبق،
  یک جدولِ نتایجِ ترکیبی.
- **Scan one at a time** — هر CIDR منطبق به‌عنوان یک گامِ مستقلِ همان Job واحد،
  به‌ترتیب و پشت‌سرهم اسکن می‌شود (اسکنِ CIDR بعدی فقط پس از پایانِ قبلی شروع می‌شود).
  پیشرفت و نتایجِ هر CIDR جداگانه و به‌محضِ آماده‌شدن (از طریق همان مکانیزمِ polling
  موجود) نمایش داده می‌شود، نه فقط در پایانِ کار. اگر اسکنِ یک CIDR با خطا مواجه شود،
  فقط همان CIDR به‌عنوان ناموفق علامت می‌خورد و بقیهٔ صف متوقف نمی‌شود (fail-soft).
  در هر دو حالت، همهٔ CIDRهای اسکن‌شده در یک اسکنِ واحد ذخیره می‌شوند، بنابراین دکمه‌های
  «Download whitelist IPs» و دانلود CSV/JSON در هر دو حالت یکسان کار می‌کنند.

The Live Scan page now offers an explicit choice before starting: **"Scan all
together"** (default, unchanged — one job, one combined table) or **"Scan one
at a time"** (each matched CIDR runs as its own sequential step of the same
job; per-CIDR progress/results appear as soon as each one finishes via the
existing polling mechanism, and a failure in one CIDR never aborts the rest of
the queue). Either way, all scanned CIDRs are persisted as a single scan, so
the download/export buttons behave identically in both modes.

**تست تقریبیِ مسیر به یک مقصدِ دلخواه (Proximity ping / RIPE Atlas):**

اندازه‌گیریِ «پینگِ خودِ یک IP کشف‌شده به یک مقصد ثالث» از بیرون **ممکن نیست** — فقط
مالکِ آن IP می‌تواند آن را وادار به ارسال ترافیک کند؛ این یک محدودیت این ابزار نیست،
بلکه یک واقعیتِ بنیادیِ شبکه است. نزدیک‌ترین تقریبِ صادقانه: از **RIPE Atlas** (یک
پلتفرمِ عمومیِ اندازه‌گیریِ اینترنت) خواسته می‌شود نزدیک‌ترین پروبِ خود به شبکهٔ آن IP
(بر اساس تطبیقِ ASN) را پیدا کند و از همان پروب یک پینگِ یک‌بارهٔ به مقصدِ دلخواه بزند.

روی هر ردیفِ نتیجهٔ اسکن، یک دکمهٔ **«Test path to…»** جداگانه و اختیاری وجود دارد
(کاملاً مجزا از ستون‌های دسترس‌پذیریِ دوطرفهٔ ایران/خارج، چون مفهومِ متفاوتی را می‌سنجد).
این قابلیت به همان `GAMING_RIPE_ATLAS_KEY` نیاز دارد؛ بدون کلید، پیامِ روشنِ «پیکربندی
نشده» نمایش داده می‌شود. هر نتیجه — همیشه — با این هشدار همراه است:

> «تقریبی — از نزدیک‌ترین پروبِ در دسترسِ RIPE Atlas به شبکهٔ این IP اندازه‌گیری شده،
> نه از خودِ IP.»

اگر هیچ پروبی نزدیکِ شبکهٔ آن IP نباشد، پیامِ روشنِ «no nearby probe available» نشان
داده می‌شود — هرگز به‌جای آن از یک پروبِ نامرتبط استفاده نمی‌شود.

Measuring a discovered IP's *own* ping to a third-party destination is not
possible from the outside — only that IP's operator can make it originate
traffic; this is a fact about networking, not a limitation of this tool. As
the closest honest approximation, an opt-in **"Test path to…"** button on each
scan row (kept explicitly separate from the Iran/abroad reachability columns)
asks the nearest RIPE Atlas probe to that IP's network (by ASN match) to ping
a destination you choose. Gated behind `GAMING_RIPE_ATLAS_KEY`; every result
always carries the disclaimer "Approximate — measured from the nearest
available RIPE Atlas probe to this IP's network, not from the IP itself," and
a network with no nearby probe is reported as such, never silently swapped for
an unrelated one.

**نکات امنیتی (Security notes):**

- در نخستین اجرا یک **نام کاربری و رمز تصادفی** ساخته و **فقط یک‌بار** چاپ می‌شود؛
  رمز به‌صورت هش‌شده (`pbkdf2_hmac` + salt) ذخیره می‌شود و دیگر قابل بازیابی نیست.
  آن را همان لحظه ذخیره کنید. On first run a random username/password is generated
  and printed **once**; the password is stored only as a salted hash.
- پیش‌فرض `--bind 0.0.0.0` داشبورد را روی همهٔ رابط‌های شبکه در دسترس می‌گذارد. روی
  شبکهٔ نامطمئن یا از `--tls` استفاده کنید یا با `--bind 127.0.0.1` محدود کنید و از
  طریق تونل SSH وصل شوید. `0.0.0.0` over plain HTTP is warned about at startup.
- ورود ناموفق به‌ازای هر IP نرخ‌محدود می‌شود تا حملهٔ brute-force کند شود. برای
  اسکریپت/اتوماسیون می‌توانید از توکن Bearer (`Authorization: Bearer …`) استفاده کنید.
- تغییر رمز از داخل داشبورد، رمز فعلی را می‌خواهد و همهٔ نشست‌های باز را باطل می‌کند.

**اجرای دائمی بدون نیاز به باز ماندن SSH (Keep it running after you disconnect):**

به‌طور پیش‌فرض `gaming web` تا وقتی زنده می‌ماند که ترمینال/نشست SSH بازکنندهٔ آن باز
بماند؛ با بستن SSH فرایند هم بسته می‌شود. برای اینکه داشبورد مستقل از ترمینال روی سرور
بماند دو راه دارید:

By default `gaming web` only stays up while the terminal/SSH session that
launched it stays open. To keep the dashboard running after you disconnect,
use either the built-in `--daemon` flag (quick) or a systemd service (robust).

```bash
# روش سریع: اجرای پس‌زمینه و جدا از ترمینال. نام کاربری/رمز پیش از رفتن به پس‌زمینه
# چاپ می‌شوند؛ خروجی در web.log و شناسهٔ فرایند در web.pid (پوشهٔ دادهٔ برنامه) ذخیره می‌شود.
gaming web --daemon --bind 127.0.0.1 --port 8787

# وضعیت اجرا را ببینید (آیا در حال اجراست و از چه زمانی)
gaming web --status

# توقف تمیز فرایند پس‌زمینه (SIGTERM، و در صورت لزوم SIGKILL)
gaming web --stop
```

`--daemon` فقط بقای فرایند پس از قطع اتصال را تغییر می‌دهد؛ رفتار پیش‌فرض `--bind`
و احراز هویت دست‌نخورده می‌ماند. روی ویندوز (بدون `os.fork`) این گزینه با پیام روشن
خطا می‌دهد و شما را به سرویس‌منیجر ارجاع می‌دهد. `--daemon` changes only whether the
process survives disconnection — not the bind/auth behavior.

برای راه‌اندازی پایدارتر (بقا پس از ری‌بوت و ری‌استارت خودکار در صورت کرش) از فایل نمونهٔ
systemd به نشانی `packaging/gaming-web.service` استفاده کنید (به‌صورت خودکار نصب نمی‌شود).
For a more permanent/production setup, use the shipped systemd unit template
`packaging/gaming-web.service` (not auto-installed):

```ini
# /etc/systemd/system/gaming-web.service  (خلاصه — فایل کامل در packaging/)
[Service]
User=gaming
ExecStart=/opt/gaming/.venv/bin/gaming web --bind 127.0.0.1 --port 8787
Restart=on-failure
Environment=GAMING_HOME=/var/lib/gaming
```

```bash
sudo cp packaging/gaming-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gaming-web
# اعتبارنامهٔ نخستین اجرا در ژورنال چاپ می‌شود:
sudo journalctl -u gaming-web --no-pager | grep -A6 'web dashboard'
```

---

### اسکن زمان‌بندی‌شده و هشدار تغییر وضعیت (`gaming schedule`)

برای پایش پیوسته، می‌توانید یک اسکن ذخیره‌شده را روی یک بازهٔ زمانی به‌صورت
خودکار تکرار کنید. هر اجرا در تاریخچه ذخیره می‌شود و نمودار روند داشبورد را
بدون اجرای دستی پر می‌کند.

```bash
# هر ۱۵ دقیقه رِنج‌های ایران را دوباره اسکن کن (تا وقتی Ctrl-C بزنید)
gaming schedule iran --interval 900

# دقیقاً ۳ بار اسکن کن و خارج شو (برای cron/CI)
gaming schedule foreign --interval 300 --count 3
```

اگر در **Settings** گزینهٔ `alert_on_change` را روشن کنید، هر بار که یک میزبان
بین وضعیت «سفیدلیست» (`INTERNATIONAL`) و وضعیت‌های تنزل‌یافته
(`IRAN_ONLY`/`ABROAD_ONLY`/`UNREACHABLE`) جابه‌جا شود، در لاگ گزارش می‌شود؛
و اگر `alert_webhook_url` را هم تنظیم کنید، یک payload از نوع JSON با
`urllib` به آن آدرس POST می‌شود. هر دو پیش‌فرض **خاموش**‌اند.

A scheduled scan re-runs a saved scope on an interval and appends each run to
history. With `alert_on_change` on (opt-in), a host flipping between the
`INTERNATIONAL` whitelist and a degraded verdict is logged, and an optional
`alert_webhook_url` receives a JSON POST (stdlib `urllib`).

### به‌روزرسانی دادهٔ منابع (`gaming refresh-seeds`)

فهرست منابعِ داخلی (`providers.toml`) را در برابر پیشوندهای فعلاً اعلام‌شدهٔ BGP
بازبینی می‌کند و رِنج‌هایی را که دیگر اعلام نمی‌شوند **علامت‌گذاری** می‌کند
(هیچ‌چیز حذف نمی‌شود). فقط-خواندنی و کاملاً fail-soft است.

```bash
gaming refresh-seeds            # بازبینی همهٔ منابع بستهٔ داخلی
gaming refresh-seeds --timeout 20
```

### اعتبارسنجی و نشانهٔ تازگی دادهٔ منابع (`gaming validate-seed`)

مثل `refresh-seeds` رِنج‌های داخلی را در برابر پیشوندهای اعلام‌شده بررسی می‌کند،
اما علاوه بر گزارش، تاریخ امروز را در یک فیلد جدید `[meta].last_validated` داخل
`providers.toml` **ثبت** می‌کند تا مشخص باشد دادهٔ داخلی چقدر تازه است. این دستور
هیچ‌گاه رکورد یک ارائه‌دهنده را اضافه، ویرایش یا حذف نمی‌کند؛ فقط همان یک خطِ نشانه
را بازنویسی می‌کند و بلوک‌های `[[provider]]` دست‌نخورده می‌مانند. نشانه فقط وقتی ثبت
می‌شود که دست‌کم یک ارائه‌دهنده واقعاً از شبکه پاسخ گرفته باشد (یک اجرای کاملاً
آفلاین ادعای تازگی نمی‌کند). دستور `gaming sources` نشان می‌دهد آخرین اعتبارسنجی
کِی بوده است.

```bash
gaming validate-seed              # اعتبارسنجی + ثبت تاریخ در نشانه
gaming validate-seed --no-marker  # فقط گزارش، بدون تغییر نشانه
gaming sources                    # فهرست منابع + «seed data last validated: …»
```

`validate-seed` re-checks the bundled seed CIDRs against announced BGP prefixes
(like `refresh-seeds`) and additionally stamps today's date into a new
`[meta].last_validated` field in `providers.toml`, so you can see how fresh the
bundled data is. It **only reports** stale CIDRs and updates that one marker line
— it never adds, edits, or deletes a provider entry. The marker is stamped only
when at least one provider was actually reachable. `gaming sources` prints the
marker.

### انتخاب سرویسِ بررسی «از خارج» و RIPE Atlas (`abroad_provider`)

بررسی «آیا این IP از خارج از ایران در دسترس است؟» دیگر به یک سرویسِ شخص ثالثِ
تک‌نقطه‌ای وابسته نیست. این بررسی پشتِ یک واسط قرار گرفته و دو پیاده‌سازی دارد:

- **check-host.net** (پیش‌فرض) — همان منطق قبلی، بدون تغییر.
- **RIPE Atlas** — یک سکوی اندازه‌گیریِ رسمی و پایدارتر. کاملاً **اختیاری** است و به
  یک کلید API نیاز دارد که از متغیر محیطیِ `GAMING_RIPE_ATLAS_KEY` خوانده می‌شود
  (هرگز داخل کد ذخیره نمی‌شود). اگر کلیدی تنظیم نشده باشد، این ارائه‌دهنده نادیده
  گرفته می‌شود و ابزار بدونِ هیچ تغییری فقط از check-host.net استفاده می‌کند.

از منوی **Settings** (یا کلید `global_check.provider` در فایل پیکربندی) می‌توانید
یکی از `check-host`، `ripe-atlas` یا `both` را انتخاب کنید. در حالت `both` شمارشِ
گره‌های موفق/کل از هر دو سرویس **پیش از** اعمالِ آستانه با هم جمع می‌شوند، بنابراین
قطعی یا محدودیت‌نرخِ یک سرویس به‌تنهایی نتیجه را تعیین نمی‌کند.

همچنین اکنون «قطع بودن سرویس» از «در دسترس نبودن مقصد» **جدا** نمایش داده می‌شود:
ستون ABROAD می‌تواند `unavailable` (سرویس بررسی از کار افتاده)، `not checked`
(بررسی نشده/غیرعمومی) یا `OK/FAIL (n/total)` (پاسخِ واقعی) باشد — پس می‌فهمید
«check-host.net الان قطع است» با «این IP از خارج در دسترس نیست» فرق دارد.

```bash
# استفاده از هر دو سرویس با کلید RIPE Atlas
export GAMING_RIPE_ATLAS_KEY="<your-atlas-key>"
# سپس از منوی Settings مقدار abroad_provider را روی both بگذارید
```

The abroad ("reachable from outside Iran?") check now sits behind a provider
interface with two implementations: **check-host.net** (default, unchanged) and
an optional **RIPE Atlas** provider (a more authoritative platform). RIPE Atlas
needs an API key from the `GAMING_RIPE_ATLAS_KEY` environment variable (never
hardcoded); with no key it is skipped and the tool falls back to check-host.net
with no behaviour change. Choose `check-host`, `ripe-atlas`, or `both` via
Settings (`abroad_provider`) or the `global_check.provider` config key. With
`both`, node-ok/node-total counts are summed across providers **before** the
threshold, so one service's outage doesn't decide the verdict. A provider outage
now shows as a distinct `unavailable` state, separate from `not checked` and from
a real `FAIL`.

### اسکن پورت‌های رایج (اختیاری)

از منوی **Settings** (یا فرم Settings در داشبورد) می‌توانید `scan_ports` را روشن
و فهرست `ports` را تنظیم کنید تا هر میزبانِ زنده علاوه بر تأخیر، یک پروبِ سادهٔ
TCP-connect روی پورت‌های رایج (پیش‌فرض `80,443,22,...`) هم بگیرد؛ پورت‌های باز در
همان جدول نتایج (کنسول و وب) نشان داده می‌شوند. این پروب مستقل و fail-soft است و
هرگز اسکن اصلی را کند یا متوقف نمی‌کند.

---

## اسکن چطور کار می‌کند؟

روند کار در حالت تعاملی به این صورت است:

1. **انتخاب دامنه (scope):** رِنج‌های ایران یا خارجی. هر دامنه یک فهرست CIDRِ داخلی و
   قابل‌ویرایش دارد (از منوی «Manage IP ranges» می‌توانید رِنج دلخواه اضافه/حذف کنید).
2. **نمونه‌برداری از میزبان‌ها:** برای اینکه اسکن روی رِنج‌های بزرگ سریع بماند، به‌جای
   آزمودنِ تک‌تکِ آدرس‌ها، از هر رِنج تعدادی نمونه انتخاب می‌شود (این مقدار در «Settings» قابل‌تنظیم است).
3. **کشف IPهای زنده (اختیاری):** یک پروبِ سریعِ تک‌مرحله‌ای، میزبان‌های پاسخگو را پیدا می‌کند.
   می‌توانید همان‌جا این فهرست را به یک **اسکن کاملِ سلامت** ارتقا دهید.
4. **اندازه‌گیری سلامت:** برای هر میزبان، **تأخیر (latency)** و **درصد بسته‌های گم‌شده
   (packet loss)** به‌صورت چندسکویی اندازه‌گیری می‌شود (پینگ ICMP و در صورت لزوم fallback به TCP).
   یک **نوار پیشرفتِ زنده** با شمارشِ لحظه‌ایِ GOOD/MEDIUM/BAD نمایش داده می‌شود.
5. **دسته‌بندی نتیجه:** بر اساس تأخیر و درصد اتلاف، هر میزبان یکی از این سه برچسب را می‌گیرد:

  | برچسب | معنی |
  |---|---|
  | **GOOD** | قابل‌دسترس، تأخیر کم، اتلاف بستهٔ کم. |
  | **MEDIUM** | قابل‌دسترس، اما تأخیر بالاتر یا اتلاف متوسط. |
  | **BAD** | غیرقابل‌دسترس یا اتلاف بستهٔ بالا. |

6. **ذخیرهٔ تاریخچه:** نتیجهٔ هر اسکن در یک پایگاه‌دادهٔ محلی SQLite ذخیره می‌شود و از
   «View scan history» بین اجراهای مختلف قابل‌مرور است.

آستانه‌های تأخیر/اتلاف، تعداد پروب به‌ازای هر میزبان، میزان هم‌زمانی، زمان‌انتظار و
اندازهٔ نمونه‌برداری همگی از منوی **Settings** قابل‌تنظیم‌اند.

### محل ذخیرهٔ داده‌ها

وضعیت برنامه در پوشهٔ دادهٔ کاربر نگهداری می‌شود (با متغیر محیطی `GAMING_HOME` قابل تغییر است):

| سیستم‌عامل | مسیر |
|---|---|
| ویندوز | `%LOCALAPPDATA%\gaming\` |
| لینوکس/مک | `$XDG_DATA_HOME/gaming/` یا `~/.local/share/gaming/` |

این پوشه شامل این فایل‌هاست: `history.db` (تاریخچهٔ اسکن)، `settings.json` (تنظیمات و آستانه‌ها)
و `custom_ranges.txt` (رِنج‌های دلخواهی که خودتان اضافه کرده‌اید).

---

## گرفتن خروجی از نتایج

**حالت تعاملی:** نتیجهٔ هر اسکن به‌صورت خودکار در پایگاه‌دادهٔ محلی SQLite ذخیره می‌شود و
هر زمان از منوی **View scan history** قابل‌مرور است — نیازی به کار اضافه نیست.

**حالت خط‌فرمان:** برای گرفتن خروجی در قالب‌های ساختاریافته، از فلگ `--format` و برای نوشتن
در فایل از `--output` (یا `-o`) استفاده کنید:

```bash
# خروجی JSON در فایل
gaming --offline run --country IR --format json -o report.json

# خروجی CSV در فایل
gaming --offline discover --foreign-datacenter --format csv -o foreign.csv

# خروجی خوانا در کنسول
gaming check 1.1.1.1 --ports 80,443 --format console
```

هر ردیفِ نتیجه (در کنسول/JSON/CSV) شامل این فیلدهاست:

| فیلد | معنی |
|---|---|
| `source` | منبع/منابعی که این رکورد را تولید کرده‌اند (مثلاً `rdap+whois`). |
| `asn` | شمارهٔ سیستم خودمختار به شکل `AS<n>`. |
| `organization` | سازمان مالک (در صورت مشخص‌بودن). |
| `country` | کد کشور (ISO). |
| `provider` | نشانهٔ ارائه‌دهنده (حروف کوچک، قابل‌جستجوی بخشی). |
| `prefix` | CIDRِ نرمال‌شده. |
| `alive` | دسترس‌پذیری محلی (`true` / `false` / `null`). |
| `global_reachable` | دسترس‌پذیری سراسری از طریق check-host.net (`true` / `false` / `null`). |
| `open_ports` | پورت‌هایی که هنگام پروب باز بوده‌اند. |
| `notes` | یادداشت‌های منشأ/تشخیصی. |

---

## پیکربندی (اختیاری)

پیکربندی لایه‌لایه است: **پیش‌فرض‌های داخلی ← فایل TOML ← بازنویسی از طریق خط‌فرمان**.
یک قالبِ کامل و توضیح‌دار در [`gaming.example.toml`](gaming.example.toml) هست:

```bash
gaming --config gaming.example.toml run --format json
```

بخش‌ها: `[general]`، `[discovery]`، `[filters]`، `[reachability]`، `[global_check]`.

گزینه‌های سراسری (پیش از نام زیر‌دستور):

| گزینه | توضیح |
|---|---|
| `--config, -c PATH` | مسیر فایل پیکربندی TOML. |
| `--log-level LEVEL` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR`. |
| `--concurrency N` | حداکثر تعداد کارگرِ هم‌زمان. |
| `--timeout SECONDS` | زمان‌انتظار برای هر عملیات. |
| `--offline` | استفاده از دادهٔ نمونهٔ داخلی به‌جای فراخوانی زندهٔ شبکه. |
| `--quiet, -q` | فقط خطاها را لاگ کن. |
| `--version` | چاپ نسخه. |

---

## پیش‌نیازها

- **پایتون ۳٫۱۱ یا بالاتر**.
- بدون وابستگیِ خارجیِ زمان‌اجرا (فقط کتابخانهٔ استاندارد).
- برای پینگِ ICMP، دسترسی به دستور `ping` سیستم‌عامل لازم است؛ در صورت نبودِ آن،
  به‌صورت خودکار به بررسی TCP سوییچ می‌شود.
- برای بررسی‌های سراسری (`--global`) و کشف زنده، دسترسی به اینترنت لازم است.

---

## محدودیت‌ها

- این ابزار **رِنج‌های کاملِ IP را به‌طور جامع پویش نمی‌کند**؛ برای حفظ سرعت روی رِنج‌های
  بزرگ، از هر رِنج فقط **نمونه‌برداری** می‌کند (اندازهٔ نمونه در «Settings» قابل‌تنظیم است).
- دقتِ پینگِ ICMP و درصد اتلاف بسته به شبکه، فایروال و سیستم‌عامل شما بستگی دارد؛ برخی
  میزبان‌ها ممکن است ICMP را مسدود کنند و در نتیجه به‌اشتباه **BAD** دیده شوند.
- کشفِ زندهٔ منابع (RDAP/WHOIS/BGP/PeeringDB) به سرویس‌های عمومیِ شخص ثالث وابسته است؛
  در صورت شکست یا با فلگ `--offline`، ابزار به **دادهٔ نمونهٔ داخلی** برمی‌گردد و خروجی
  ممکن است کامل یا به‌روز نباشد.
- بررسی سراسری، آدرس‌های هدف را به یک سرویس شخص ثالث (check-host.net) ارسال می‌کند و
  فقط روی IPهای عمومی کار می‌کند.
- این ابزار برای **شناساییِ شبکه و تست دسترس‌پذیری** است. فقط روی شبکه‌ها و میزبان‌هایی
  از آن استفاده کنید که مالک آن‌ها هستید یا صراحتاً مجاز به بررسی آن‌ها شده‌اید.

---

## معماری پروژه

```
src/gaming/
├── cli.py               # زیر‌دستورهای argparse (menu/sources/discover/check/run/web/schedule/…)
├── pipeline.py          # هماهنگی: کشف ← پردازش ← دسترس‌پذیری
├── config.py            # بارگذاری TOML + بازنویسی لایه‌ای (tomllib)
├── models.py            # IPRecord، Filters، توابع نرمال‌سازی
├── logging_setup.py     # پیکربندی لاگ
├── discovery/           # منابع افزونه‌ای (واسط مشترک Source)
│   ├── base.py          #   Source ABC + DiscoveryContext + fallback آفلاین
│   ├── rdap.py  whois.py  asn_bgp.py  peeringdb.py  rir.py
├── processing/
│   ├── normalize.py     # حذف تکراری، ادغام متادیتا، فشرده‌سازی پیشوند
│   └── filters.py       # کشور/ASN/ارائه‌دهنده/سازمان + تمرکز ایران/خارجی
├── reachability/
│   ├── local.py         # بررسی زنده‌بودن ping/tcp/auto (هم‌زمان)
│   ├── ports.py         # پروب پورت TCP
│   └── global_check.py  # واسط AbroadProvider: check-host.net + RIPE Atlas (اختیاری)
├── reporting/
│   ├── console.py  json_export.py  csv_export.py
├── interactive/         # اسکنر تعاملی و منویی سلامت IP
│   ├── menu.py          #   حلقهٔ نازکِ منو (فقط ورودی/خروجی + dispatch)
│   ├── actions/         #   منطقِ هر عمل، جدا از ترمینال (scan/discover/history/…)
│   ├── scanner.py       #   رِنج‌ها ← جستجوی زنده ← اسکن تأخیر/خارج/پورت ← دسته‌بندی
│   ├── pinger.py        #   اندازه‌گیری چندسکویی تأخیر و اتلاف
│   ├── classify.py      #   امتیازدهی GOOD / MEDIUM / BAD + CombinedResult دوطرفه
│   ├── ranges.py        #   فهرست‌های CIDR داخلی و قابل‌ویرایش ایران/خارجی
│   ├── storage.py       #   نگهداری تاریخچهٔ اسکن در SQLite (مهاجرتِ افزایشی)
│   ├── scheduler.py     #   اسکن‌های زمان‌بندی‌شدهٔ دوره‌ای
│   ├── alerts.py        #   تشخیص تغییر وضعیت + webhook اختیاری
│   ├── providers.py     #   دادهٔ منابع + refresh/validate و نشانهٔ last_validated
│   ├── filters_shared.py #  توابعِ مشترکِ منو و وب (octet/بره‌ایپی/جست‌وجوی جزئی)
│   ├── settings.py  progress.py  report.py  paths.py
│   └── data/            #   providers.toml و فهرست‌های داخلیِ رِنج
├── web/                 # داشبورد وب محلی (server/handlers/jobs/auth/summary + static)
└── utils/http.py        # HTTP مبتنی بر کتابخانهٔ استاندارد با retry/timeout
```

> برای شرحِ کاملِ معماری (خط‌لوله، تفاوتِ مسیر CLI و منو، شمای SQLite، تفکیکِ
> `Config`/`Filters` از `Settings`، و واسط ارائه‌دهنده‌های بررسیِ خارج) به
> [`docs/architecture.md`](docs/architecture.md) نگاه کنید.

**اصول طراحی:** ماژولار و توسعه‌پذیر (افزودن یک منبع با پیاده‌سازی `Source` و ثبت آن)،
بدون وابستگی، خرابی‌پذیرِ نرم (یک منبع یا میزبانِ خراب هرگز کل اجرا را متوقف نمی‌کند)، و
کاملاً قابل‌آزمون به‌صورت آفلاین از طریق تزریق وابستگی و دادهٔ نمونهٔ داخلی.

---

## تست و توسعه

```bash
python -m pytest                    # یا: PYTHONPATH=src python -m pytest
python -m pip install -e ".[dev]"   # نصب همراه با ابزارهای توسعه
make check                          # لینت + تست (دروازهٔ CI)
make cov                            # تست همراه با گزارش پوشش
make build                          # ساخت sdist + wheel و بررسی با twine
```

مجموعهٔ تست کاملاً آفلاین است (بدون تماس واقعی با شبکه): منابع از دادهٔ نمونهٔ داخلی استفاده
می‌کنند و دسترس‌پذیری monkeypatch می‌شود. برای جزئیات گردش‌کار به
[CONTRIBUTING.md](CONTRIBUTING.md) و برای تاریخچهٔ نسخه‌ها به [CHANGELOG.md](CHANGELOG.md) نگاه کنید.

---

## استفادهٔ مسئولانه

این ابزار شناساییِ شبکه و تست دسترس‌پذیری انجام می‌دهد. فقط روی شبکه‌ها و میزبان‌هایی از آن
استفاده کنید که مالک آن‌ها هستید یا صراحتاً مجاز به بررسیِ آن‌ها شده‌اید. بررسی‌های سراسری،
آدرس‌های هدف را به یک سرویس شخص ثالث (check-host.net) ارسال می‌کنند.

## مجوز

MIT — به [LICENSE](LICENSE) نگاه کنید.
