# v0.8.0 — Real Ctrl+C reliability + visual/UX overhaul

**This release is about two things: making Ctrl+C actually shut the web panel
down cleanly, and a genuine visual/UX overhaul across the dashboard, the
terminal, and the docs.**

---

## 🇬🇧 English

### Fixed — Ctrl+C now actually works (the v0.7.0 fix was incomplete)

v0.7.0 moved `serve_forever()` onto a background thread and wrapped the wait in
`try/except KeyboardInterrupt`. That fixed the narrow case it was tested
against and left three real failure modes — each reproduced and measured before
this fix:

1. **`SIGTERM` got no cleanup at all.** `gaming web --stop` signals the daemon
   with `SIGTERM` via the PID file, but `SIGTERM` does not raise
   `KeyboardInterrupt`, so the `except` clause never ran. The process was
   killed outright: no `shutdown()`, no `server_close()`, no PID-file removal.
   Measured: exit in 0.00s with cleanup skipped entirely.
2. **In-flight scan jobs were abandoned mid-write.** `JobManager` exposed only
   `start`/`get` — there was no way to enumerate, cancel, or join job threads,
   and they were created `daemon=True`. On shutdown `serve()` returned while a
   scan thread was still running, and the interpreter then killed it at exit,
   potentially mid-SQLite-write. **This is the scenario behind the "panel just
   dies" report:** pressing Ctrl+C during a Live Scan.
3. **A `KeyboardInterrupt` caught in one thread proves nothing about the
   others.** The interrupt is delivered at an arbitrary bytecode boundary in
   the main thread; catching it at a single call site said nothing about the
   scheduler or job threads still touching the database.

Replaced with `gaming.web.lifecycle.ShutdownCoordinator`: a real
`signal.signal()` handler for both `SIGINT` and `SIGTERM` that stops the
listener from a separate thread (calling `shutdown()` from the
`serve_forever()` thread deadlocks), cancels and bounded-joins in-flight job
threads, stops the scan scheduler, releases the listening socket, removes the
PID file, and only then prints a final `Web panel stopped.` The handler itself
only sets a flag and returns — the multi-second drain happens on the waiting
thread, never inside a signal handler.

This is now the **single** shutdown path: `gaming web`, the interactive menu's
"Launch web panel" option, and `daemon.stop()`'s `SIGTERM` all route through
the same coordinator instead of three implementations that could drift apart.

**Also fixed:**

- **Background jobs are cooperatively cancellable.** `Job.cancelled()` lets
  long-running workers stop at a safe point; the sequential scan loop polls it
  between CIDRs, so a shutdown mid-scan stops after the current CIDR and still
  persists what it completed. Jobs that ignore cancellation are bounded by a
  drain timeout and honestly reported (`N background job(s) did not stop in
  time`) rather than silently dropped.
- **An immediate restart on the same port works** — `server_close()` is now
  guaranteed to run, so no more "address already in use".
- **`serve()` can no longer hang** if the serve loop exits on its own — a
  latent deadlock caught by the new tests.
- Repeated Ctrl+C escalates to an immediate exit (code 130), and the previous
  signal handlers are restored afterwards so the interactive menu keeps
  responding to Ctrl+C once the panel stops.
- `daemon.stop()`'s grace period raised 5s → 15s so a clean drain is not cut
  short by the `SIGKILL` escalation.

### Changed — visual/UX overhaul

**Web dashboard.** Still stdlib-served with no build step, no CDN, and no
webfonts — it renders identically on an air-gapped host.

- A deliberate dark palette driven entirely by CSS custom properties in a
  single `:root` block; nothing below it hardcodes a colour, so the theme is
  retargetable from one place. One accent is used consistently for primary
  actions, the active nav item, and focus rings.
- Monospace for operator data (hosts, CIDRs, ports, latency) and a sans stack
  for UI chrome, on a 4px spacing rhythm.
- A persistent sidebar with a clear active-page indicator, plus a header
  showing session identity and live connection status. Related controls are
  grouped into cards/panels instead of floating as bare form elements.
- Tables gained sticky headers for long result sets, subtle row banding and
  hover, right-aligned tabular numerics for latency and node counts, and a sort
  caret on the active column. Status values render as pill badges with
  consistent colours across GOOD/MEDIUM/BAD and
  INTERNATIONAL/IRAN_ONLY/ABROAD_ONLY/UNREACHABLE.
- Real empty, loading, and error states: placeholders that explain what to do
  next rather than a blank table, a determinate progress bar driven by the
  existing job-polling `progress` field, and styled banners instead of raw
  error strings. A scan interrupted by shutdown reports the new `cancelled`
  status explicitly.
- Responsive down to narrow desktop widths — the sidebar folds into a
  horizontal top nav. Honours `prefers-reduced-motion`.

**Terminal UI.** New `gaming.interactive.theme` holds the ANSI palette (as
semantic roles) and the single column-aligned table renderer used across the
whole terminal experience.

- Replaces four separate ad-hoc `ljust` loops that had drifted apart in padding
  and header style.
- Numeric columns (latency, loss, counts, scan IDs) are now right-aligned.
- The main menu, sub-menus, and prompts are styled coherently with the banner.
- `gaming sources` prints a real table with a description per source;
  `gaming validate-seed` reports stale CIDRs as a table.
- All styling routes through the existing `_supports_color` predicate, so
  piped, redirected, `NO_COLOR`, and non-TTY output stays clean plain ASCII. A
  regression test asserts the invariant directly: **stripping ANSI from the
  coloured rendering yields byte-for-byte the plain rendering**, so colour can
  never disturb column alignment.

**Documentation.** `README.md` (English) and `README.fa.md` (Persian) are now
true parallel versions with identical structure, cross-linked at the top —
previously a single mixed-language file. Bidirectional reachability, the web
dashboard, and scheduled monitoring are described up front with their own
sections. A new "What the output actually looks like" section shows a real
terminal results table, the live progress bar, and an ASCII mockup of the
dashboard. All 23 example commands were verified against the actual CLI.

### Notes

- No API endpoint, response shape, or behaviour changed beyond the shutdown fix
  itself.
- Stdlib only — no new runtime dependencies, no frontend build tooling.
- Tests: **309 → 332 passing**, lint clean. Includes a fix for three
  environment-dependent test bugs that made the suite fail on macOS.

---

## 🇮🇷 فارسی

**این نسخه دربارهٔ دو چیز است: اینکه Ctrl+C واقعاً پنل وب را تمیز متوقف کند، و یک
بازطراحیِ واقعیِ ظاهر و تجربهٔ کاربری در داشبورد، ترمینال و مستندات.**

### رفع اشکال — حالا Ctrl+C واقعاً کار می‌کند (اصلاحِ نسخهٔ ۰٫۷٫۰ ناقص بود)

نسخهٔ ۰٫۷٫۰ حلقهٔ `serve_forever()` را به یک نخِ پس‌زمینه برد و انتظار را در
`try/except KeyboardInterrupt` پیچید. این کار فقط همان حالتِ محدودی را که آزموده
شده بود درست کرد و سه خرابیِ واقعی باقی ماند — هرکدام پیش از این اصلاح بازتولید و
اندازه‌گیری شدند:

۱. **`SIGTERM` هیچ پاک‌سازی‌ای نداشت.** دستور `gaming web --stop` با `SIGTERM` از
   طریق فایل PID سیگنال می‌دهد، اما `SIGTERM` خطای `KeyboardInterrupt` تولید
   نمی‌کند، پس بلوکِ `except` هرگز اجرا نمی‌شد. فرایند مستقیماً کشته می‌شد: نه
   `shutdown()`، نه `server_close()`، نه حذفِ فایل PID. اندازه‌گیری‌شده: خروج در
   ۰٫۰۰ ثانیه با پاک‌سازیِ کاملاً نادیده‌گرفته‌شده.
۲. **کارهای اسکنِ در حال اجرا وسطِ نوشتن رها می‌شدند.** کلاس `JobManager` فقط
   `start`/`get` را در اختیار می‌گذاشت — هیچ راهی برای فهرست‌کردن، لغو یا پیوستن به
   نخ‌های کار نبود و این نخ‌ها با `daemon=True` ساخته می‌شدند. هنگام توقف، تابع
   `serve()` بازمی‌گشت در حالی که نخِ اسکن هنوز در حال اجرا بود و مفسر آن را هنگام
   خروج می‌کشت، احتمالاً وسطِ نوشتن در SQLite. **سناریوی گزارش‌شدهٔ «پنل همین‌طوری
   می‌میرد» دقیقاً همین است:** فشردن Ctrl+C در میانهٔ Live Scan.
۳. **گرفتنِ `KeyboardInterrupt` در یک نخ، چیزی دربارهٔ نخ‌های دیگر ثابت نمی‌کند.**
   این وقفه در یک مرزِ دلخواهِ بایت‌کد در نخِ اصلی تحویل داده می‌شود؛ گرفتنِ آن در یک
   نقطه چیزی دربارهٔ زمان‌بند یا نخ‌های کاری که هنوز با پایگاه‌داده کار می‌کنند
   نمی‌گوید.

اکنون با `gaming.web.lifecycle.ShutdownCoordinator` جایگزین شده است: یک هندلرِ
واقعیِ `signal.signal()` برای هر دو `SIGINT` و `SIGTERM` که شنونده را از نخی جدا
متوقف می‌کند (فراخوانیِ `shutdown()` از نخِ `serve_forever()` باعث بن‌بست می‌شود)،
نخ‌های کارِ در حال اجرا را لغو و با انتظارِ کران‌دار join می‌کند، زمان‌بندِ اسکن را
متوقف می‌کند، سوکتِ گوش‌دهنده را آزاد می‌کند، فایل PID را حذف می‌کند و تنها پس از آن
پیام پایانیِ `Web panel stopped.` را چاپ می‌کند. خودِ هندلر فقط یک پرچم را
تنظیم می‌کند و بازمی‌گردد — تخلیهٔ چندثانیه‌ای روی نخِ منتظر انجام می‌شود، هرگز درونِ
یک signal handler.

این اکنون **تنها** مسیرِ توقف است: `gaming web`، گزینهٔ «Launch web panel» در منوی
تعاملی، و `SIGTERM`ِ `daemon.stop()` همگی از همین هماهنگ‌کننده عبور می‌کنند، به‌جای
سه پیاده‌سازیِ جدا که می‌توانستند از هم فاصله بگیرند.

**همچنین رفع شد:**

- **کارهای پس‌زمینه به‌صورت مشارکتی قابل‌لغو شدند.** متد `Job.cancelled()` به
  کارگرهای طولانی اجازه می‌دهد در یک نقطهٔ امن متوقف شوند؛ حلقهٔ اسکنِ ترتیبی آن را
  بین CIDRها بررسی می‌کند، پس توقف در میانهٔ اسکن پس از CIDRِ جاری متوقف می‌شود و
  آنچه کامل شده را ذخیره می‌کند. کارهایی که لغو را نادیده بگیرند با یک مهلتِ
  تخلیه کران‌دار شده و صادقانه گزارش می‌شوند، نه اینکه بی‌صدا رها شوند.
- **راه‌اندازیِ مجددِ فوری روی همان پورت کار می‌کند** — اجرای `server_close()` اکنون
  تضمین‌شده است، پس دیگر خطای «address already in use» رخ نمی‌دهد.
- **تابع `serve()` دیگر نمی‌تواند معلق بماند** اگر حلقهٔ سرویس‌دهی خودبه‌خود تمام
  شود — یک بن‌بستِ پنهان که با تست‌های جدید کشف شد.
- فشردنِ دوبارهٔ Ctrl+C به خروجِ فوری (کد ۱۳۰) ارتقا می‌یابد، و هندلرهای سیگنالِ قبلی
  پس از آن بازگردانده می‌شوند تا منوی تعاملی پس از توقفِ پنل همچنان به Ctrl+C پاسخ
  دهد.
- مهلتِ `daemon.stop()` از ۵ به ۱۵ ثانیه افزایش یافت تا یک تخلیهٔ تمیز با ارتقا به
  `SIGKILL` نصفه نماند.

### تغییرات — بازطراحی ظاهر و تجربهٔ کاربری

**داشبورد وب.** همچنان با کتابخانهٔ استاندارد سرو می‌شود، بدون مرحلهٔ build، بدون
CDN و بدون وب‌فونت — روی یک ماشینِ کاملاً آفلاین دقیقاً یکسان نمایش داده می‌شود.

- یک پالتِ تاریکِ سنجیده که کاملاً با CSS custom properties در یک بلوکِ `:root`
  کنترل می‌شود؛ هیچ‌چیز پایین‌تر از آن رنگی را hardcode نمی‌کند، پس تم از یک نقطه
  قابل‌تغییر است. یک رنگِ تأکیدی به‌صورت یکدست برای کنش‌های اصلی، آیتمِ فعالِ منو و
  حلقه‌های فوکوس استفاده می‌شود.
- فونتِ مونواسپیس برای دادهٔ اپراتور (میزبان، CIDR، پورت، تأخیر) و فونتِ sans برای
  عناصرِ رابط، روی یک ریتمِ فاصله‌گذاریِ ۴ پیکسلی.
- نوار کناریِ ثابت با نشانگرِ روشنِ صفحهٔ فعال، به‌همراه سرصفحه‌ای که هویتِ نشست و
  وضعیتِ زندهٔ اتصال را نشان می‌دهد. کنترل‌های مرتبط در کارت/پنل گروه‌بندی شده‌اند.
- جدول‌ها سطرِ عنوانِ چسبان برای نتایجِ طولانی، سایه‌زنیِ ملایمِ سطرها و حالتِ hover،
  ستون‌های عددیِ راست‌چین برای تأخیر و تعداد نودها، و نشانگرِ مرتب‌سازی روی ستونِ فعال
  گرفتند. وضعیت‌ها به‌صورت نشانِ قرصی‌شکل با رنگ‌بندیِ یکدست نمایش داده می‌شوند.
- حالت‌های واقعیِ خالی، بارگذاری و خطا: به‌جای جدولِ خالی، متنی که می‌گوید قدم بعدی
  چیست؛ یک نوار پیشرفتِ معین که از فیلدِ `progress` موجود تغذیه می‌شود؛ و بنرهای
  طراحی‌شده به‌جای رشته‌های خام خطا. اسکنی که با توقفِ پنل قطع شود، وضعیتِ جدیدِ
  `cancelled` را صریحاً گزارش می‌کند.
- واکنش‌گرا تا عرض‌های باریکِ دسکتاپ — نوار کناری به یک منوی افقیِ بالا تا می‌شود.
  به `prefers-reduced-motion` احترام می‌گذارد.

**رابط ترمینال.** ماژول جدید `gaming.interactive.theme` پالتِ ANSI (به‌صورت
نقش‌های معنایی) و تنها رندرِ جدولِ هم‌ترازِ مورد استفاده در کلِ تجربهٔ ترمینال را
نگه می‌دارد.

- جایگزینِ چهار حلقهٔ `ljust` پراکنده شد که در فاصله‌گذاری و سبکِ عنوان از هم فاصله
  گرفته بودند.
- ستون‌های عددی (تأخیر، اتلاف، شمارش‌ها، شناسهٔ اسکن) اکنون راست‌چین هستند.
- منوی اصلی، زیرمنوها و پرسش‌ها هماهنگ با بنر استایل‌دهی شده‌اند.
- دستور `gaming sources` یک جدولِ واقعی با توضیح برای هر منبع چاپ می‌کند و
  `gaming validate-seed` نتایجِ کهنه را به‌صورت جدول گزارش می‌دهد.
- تمامِ استایل‌دهی از همان تابعِ `_supports_color` عبور می‌کند، پس خروجیِ pipe‌شده،
  redirect‌شده، `NO_COLOR` و غیر‌TTY به ASCIIِ سادهٔ تمیز تنزل می‌یابد. یک تستِ
  رگرسیون این ثابت را مستقیماً بررسی می‌کند: **حذفِ کدهای ANSI از خروجیِ رنگی،
  دقیقاً بایت‌به‌بایت خروجیِ ساده را می‌دهد**، پس رنگ هرگز نمی‌تواند هم‌ترازیِ ستون‌ها
  را بر هم بزند.

**مستندات.** فایل‌های `README.md` (انگلیسی) و `README.fa.md` (فارسی) اکنون دو
نسخهٔ کاملاً موازی با ساختارِ یکسان و پیوندِ متقابل در بالا هستند — پیش از این یک
فایلِ واحدِ دوزبانه بود. دسترس‌پذیریِ دوطرفه، داشبورد وب و پایشِ زمان‌بندی‌شده هرکدام
بخشِ اختصاصیِ خود را در ابتدای متن دارند. بخشِ جدیدِ «خروجی واقعاً چه شکلی است؟»
جدولِ واقعیِ نتایج در ترمینال، نوار پیشرفتِ زنده و یک ماکتِ ASCII از داشبورد را
نشان می‌دهد. هر ۲۳ دستورِ نمونه در برابر CLIِ واقعی راستی‌آزمایی شد.

### یادداشت‌ها

- هیچ endpoint، شکلِ پاسخ یا رفتاری فراتر از خودِ اصلاحِ توقف تغییر نکرده است.
- فقط کتابخانهٔ استاندارد — بدون وابستگیِ زمان‌اجرای جدید و بدون ابزارِ build فرانت‌اند.
- تست‌ها: **از ۳۰۹ به ۳۳۲ تستِ موفق**، لینت تمیز. شاملِ اصلاحِ سه اشکالِ وابسته به
  محیط در تست‌ها که باعثِ شکستِ مجموعه روی macOS می‌شد.

---

**Full changelog:** https://github.com/devprogrmer/gaming/blob/main/CHANGELOG.md
