# دليل اختبار Sprint 0 على Termux

الهدف: التحقق على هاتف حقيقي من بندَي بوابة Sprint 0 اللذين لا يمكن اختبارهما على حاسوب (Section 1.6):

1. **الاستقرار الطويل:** 30 دقيقة دون انهيار أو تحذيرات أو نمو في الذاكرة.
2. **سلوك أندرويد مع العملية:** هل يقتلها Phantom Process Killer أو يجمّدها، ومتى.

> **ملاحظة:** في Sprint 0 لا يُطلق الوكيل أي عمليات فرعية، لكن Python نفسه عملية فرعية لتطبيق Termux، فهو "عملية شبحية" (Phantom) بنظر أندرويد 12+. لذلك الاختبار ذو معنى من الآن.

---

## 1. التجهيز (مرة واحدة)

**المتطلبات:** أندرويد 12 فما فوق، وTermux من F-Droid أو GitHub (نسخة Play Store قديمة ولا تُحدَّث).

```bash
pkg update && pkg upgrade -y
pkg install -y python git

# المستودع كبير (نسخة من GitHub Docs): نجلب مجلد الوكيل فقط
git clone --depth 1 --filter=blob:none --sparse \
  -b layer-2-python-brain https://github.com/mohamadsy1234/docs agent-repo
cd agent-repo && git sparse-checkout set android-agent
cd android-agent/agent
```

**السر:** في Sprint 0 لا يوجد Bridge بعد، فنولّد سر اختبار محلياً. في Sprint 1 يحل محله السر الذي يولّده Bridge.

```bash
mkdir -p ~/.config/agent
python3 -c "import secrets; print('API_SECRET=' + secrets.token_hex(32))" > ~/.config/agent/.env
chmod 600 ~/.config/agent/.env
```

**سجّل معلومات الجهاز** (تُرفق مع النتائج):

```bash
getprop ro.build.version.release; getprop ro.product.manufacturer; getprop ro.product.model
python3 --version
```

---

## 2. الفحوصات السريعة

```bash
python3 agent.py --check; echo "exit=$?"
```

المتوقع: أربعة أسطر كلها `true`، و`exit=0`.

```bash
python3 -m unittest discover -s tests -v
```

المتوقع: 13 اختباراً ناجحاً (قرابة دقيقة). اختبار التجميد (`test_process_freeze_is_not_a_hang`) مهم هنا تحديداً، لأنه يحاكي ما يفعله أندرويد بالعمليات في الخلفية.

---

## 3. سيناريوهات الـ 30 دقيقة

كل سيناريو تشغيل منفصل. أداة `tools/soak.py` تشغّل الوكيل، وتسجّل الذاكرة والخيوط والملفات المفتوحة والمعالج كل 30 ثانية في CSV، ثم توقفه بـ `SIGTERM` وتطبع تقريراً بصيغة JSON.

| السيناريو | الإعداد | التشغيل | الغرض |
| --- | --- | --- | --- |
| **A** — أمامي | الشاشة مضاءة، Termux ظاهر | `python3 tools/soak.py --minutes 30 --label A-foreground` | خط الأساس: استقرار الذاكرة دون تدخل النظام |
| **B** — خلفية بلا حماية | شغّل الأمر، ثم اخرج إلى الشاشة الرئيسية وأطفئ الشاشة | `python3 tools/soak.py --minutes 30 --label B-background` | **الأهم:** ماذا يفعل أندرويد بالعملية دون أي حماية؟ |
| **C** — خلفية مع wake-lock | `termux-wake-lock` أولاً (يتطلب تطبيق Termux:API)، ثم كما في B | `python3 tools/soak.py --minutes 30 --label C-wakelock` | هل يكفي قفل التنبيه للبقاء؟ |
| **D** — اختياري، مع إعدادات Phantom | طبّق إحدى طرق جدول 0.3 عبر adb، ثم كما في B | `python3 tools/soak.py --minutes 30 --label D-phantom-off` | أثر تعطيل قيود العمليات الفرعية |

**قبل B وC:** سجّل حالة "تحسين البطارية" لتطبيق Termux (الإعدادات ← التطبيقات ← Termux ← البطارية). القيمة تؤثر مباشرة في النتيجة.

**لا تلمس الهاتف** أثناء B وC وD. لمسه يغيّر سلوك النظام ويُفسد القياس.

---

## 4. قراءة النتائج

التقرير يُطبع في النهاية ويُحفظ أيضاً في `~/.local/state/agent/soak-<label>-<time>.json`، بجانب ملف CSV وملف stderr.

| الحقل | المعنى |
| --- | --- |
| `survived: true` | الوكيل بقي حياً حتى نهاية المدة |
| `death: "killed by SIGKILL"` | قتلته جهة خارجية دون أن يكتب شيئاً في Journal: Phantom Process Killer أو Low Memory Killer |
| `death: "exited with code 5"` | الـ Watchdog أعلن `HANG`. يستحق تحقيقاً: هل في Journal سطر `process_resumed` قبله؟ |
| `journal.process_resumed > 0` | أندرويد جمّد العملية ثم أعادها، والـ Watchdog ميّز ذلك عن التجمّد الحقيقي (السلوك الصحيح) |
| `rss_growth_after_warmup_kb` | نمو الذاكرة بعد أول 5 دقائق. الحد المقبول 2048 KB |
| `fds.start` / `fds.end` | الملفات المفتوحة. ثباتها يعني لا تسرّب |
| `gate_pass` | نجاح كل شروط البوابة معاً |

**إن قُتلت أداة soak نفسها** (يتوقف التقرير ولا يُطبع شيء): آخر سطر في ملف CSV يحدد وقت القتل تقريباً.

**تشخيص إضافي (اختياري، يتطلب adb من حاسوب):**

```bash
adb logcat -d | grep -iE "phantom|Killing .*com.termux"
```

---

## 5. معيار النجاح

- **A و C:** `gate_pass: true` إلزامي.
- **B:** النتيجة تُسجَّل كما هي، نجحت أو فشلت. فشلها متوقع على كثير من الأجهزة، وهو ما يحدد متطلبات Section 8 (آلية البقاء في Termux).
- **D:** يُشغَّل فقط إن فشل C.

## 6. ما يُرسل بعد الاختبار

- معلومات الجهاز (القسم 1).
- حالة تحسين البطارية لـ Termux.
- تقارير JSON للسيناريوهات المنفّذة.
- عند أي `death` أو `HANG`: ملف CSV وملف stderr لذلك السيناريو، وآخر 20 سطراً من `~/.local/state/agent/journal.jsonl`.
