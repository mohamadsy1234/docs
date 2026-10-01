# هندسة وكيل الذكاء الاصطناعي المستقل على أندرويد: الطبقة الثانية — عقل الوكيل (Python Agent Brain)

> **الحالة:** مسودة — Section 0 مكتوب وقيد المراجعة.
>
> تعتمد هذه الوثيقة على [الطبقة الأولى: تطبيق الجسر](01-bridge-app-architecture.md).

---

## 0. القرارات التأسيسية (Foundation Decisions)

هذا القسم يُثبّت أربع قرارات لا رجعة فيها قبل كتابة أي كود في Python. الغرض: منع تغيير المعمارية في منتصف Sprint 5، حيث تكون التكلفة مضاعفة ثلاث مرات.

القرارات مترابطة: كل قرار يُضيّق مساحة القرار الذي يليه. لذلك الترتيب إلزامي.

```text
┌──────────────────────────────────────────────────────────┐
│  0.1  مفردات الإجراءات      → العقد بين Python وBridge   │
│         │                                                │
│         ▼                                                │
│  0.2  القرار A: المُخطِّط    → من يبني الخطط؟            │
│         │                                                │
│         ▼                                                │
│  0.3  القرار B: الميتا-لوب  → أين يعيش التفكير؟          │
│         │                                                │
│         ▼                                                │
│  0.4  المحلل الثابت للخطط   → من يوقف الخطة قبل تنفيذها؟ │
└──────────────────────────────────────────────────────────┘
```

---

### 0.1 مفردات الإجراءات (Action Vocabulary)

**المشكلة:** بدون لغة مشتركة مُقنَّنة بين Python وBridge، سيُنتج كل مطور شكلاً مختلفاً من نفس الإجراء. بعد أسبوعين، لن يستطيع المُحلل الثابت تحليل شيء، لأن كل إجراء له شكل مختلف.

**القرار:** تُعرَّف مفردات مغلقة (Closed Set) من ~15 إجراءً ذرياً، بأسماء ثابتة، ومخطط JSON صارم. أي إجراء خارج هذه القائمة يُرفض في Bridge قبل التنفيذ.

#### الجدول المرجعي للإجراءات

| الإجراء | النوع | القناة المفضلة | معاملات إلزامية | معاملات اختيارية | يُحتاج Key C؟ |
| --- | --- | --- | --- | --- | --- |
| `open_app` | Navigation | Intent | `package` | `deep_link` | لا |
| `open_deep_link` | Navigation | Intent | `uri` | — | لا |
| `tap` | Interaction | A11y | `node_id` | `long_press` | لا |
| `tap_by_text` | Interaction | A11y | `text` | `exact_match` | لا |
| `set_text` | Input | A11y | `node_id`, `text` | `clear_first` | لا |
| `paste_text` | Input | A11y | `node_id` | — | لا |
| `scroll` | Interaction | A11y | `direction` | `distance_px` | لا |
| `back` | Navigation | A11y | — | — | لا |
| `home` | Navigation | A11y | — | — | لا |
| `reply_notification` | Messaging | Notification | `reply_token`, `text` | — | نعم |
| `dismiss_notification` | Cleanup | Notification | `notification_key` | — | لا |
| `send_intent_action` | Messaging | Intent | `uri`, `package` | `extras` | نعم |
| `observe` | Read-only | A11y | `target`, `depth` | `expected_version` | لا |
| `wait` | Timing | Local | `ms` | — | لا |
| `abort` | Control | Local | `reason` | — | لا |

**قواعد التصنيف:**

- الإجراءات ذات Key C = نعم لا يمكن تنفيذها إلا بعد موافقة بيومترية أو توقيع مستخدم صريح. هذا يمنع LLM من إرسال رسالة بالنيابة عن المستخدم دون إذن.
- `reply_notification` يُصنَّف "خطير" لأن الردّ النصي على شخص حقيقي فعل لا رجعة فيه.
- `observe` لا يُغيّر حالة، لكنه يُنتج بيانات — يُسجَّل دائماً في Journal.

#### مخطط JSON موحّد لكل إجراء

```json
{
  "action_id": "uuid-v4",
  "verb": "tap",
  "params": { "node_id": "node_abc123", "long_press": false },
  "context": {
    "state_version": 1043,
    "human_presence": "FREE",
    "goal_id": "reply_to_ahmed",
    "timestamp": 1775012345678
  },
  "signature": {
    "key_id": "B",
    "value": "base64...",
    "nonce": "base64...",
    "expires_at": 1775012348000
  }
}
```

**قاعدة الحسم:** `verb` واحد فقط لكل طلب. لا Batches. لماذا؟

- Batch يُخفي أي إجراء فشل.
- ACK يصبح غامضاً.
- Rollback شبه مستحيل.

إن أردت تسلسل إجراءات، أرسلها منفردة، واستخدم `state_version` للتأكد من التسلسل. تكلفة زمن الوصول (~5ms عبر AIDL) لا تبرر Batch.

#### معايير القبول لـ 0.1

- [ ] جدول الإجراءات مُثبَّت في `android-agent/action-vocabulary.md` كمرجع.
- [ ] Bridge يرفض أي `verb` غير مدرج في القائمة (`reason: "UNKNOWN_VERB"`).
- [ ] Python Client يحوي Type Enum يُطابق الجدول 1:1.
- [ ] كل إجراء له Unit Test يتحقق من: schema + سلوك معامل افتراضي + رمز خطأ واحد على الأقل.

---

### 0.2 القرار A — معمارية المُخطِّط (Planner Architecture)

**السؤال:** من يبني خطة الوكيل لتحقيق هدف؟

#### الخيارات الثلاثة

| المعيار | LLM Pure | HTN Pure | هجين (مُقترح) |
| --- | --- | --- | --- |
| زمن التخطيط | 1–10 ثوانٍ | < 50ms | < 100ms (80%) |
| موثوقية التنفيذ | 60–80% | 95%+ للأنماط المعروفة | 90%+ |
| التكلفة | عالية (Tokens) | صفر | منخفضة |
| التعامل مع المجهول | ممتاز | ضعيف | جيد |
| القابلية للتحليل الثابت | ضعيفة | عالية | عالية |
| الحاجة لإنترنت | غالباً نعم | لا | اختيارياً |

#### القرار: الهجين

الهجين بترتيب صارم:

```text
1. HTN Planner يُجرَّب أولاً بأسلوب First-Match.
   ├─ إن وجد خطة كاملة → مرّرها للمحلل الثابت (0.4).
   ├─ إن وجد خطة جزئية → استدعِ LLM لإكمال النقص فقط.
   └─ إن لم يجد شيئاً → استدعِ LLM لخطة كاملة.

2. LLM Output يُمرَّر إجبارياً على:
   ├─ Schema Validator (يطابق 0.1)
   ├─ Static Analyzer (0.4)
   └─ Safety Gate (الطبقة 1) للتوقيع
```

#### لماذا هذا الترتيب تحديداً؟

- **HTN أولاً** لأن 80% من مهام الواتساب اليومية نمطية: فتح محادثة، رد، إرسال وسائط. لا معنى لاستدعاء LLM لها.
- **LLM للمجهول** لأن الفشل النمطي يعني إما واجهة تغيّرت أو مهمة جديدة — كلاهما يستدعي تفكيراً مرناً.
- **HTN لا يُستبدل** حتى مع تحسّن LLM، لأن HTN يمنح قابلية تحليل ثابت قبل التنفيذ، وهذا ما يحتاجه Safety Gate.

#### مكتبة HTN المقترحة

`pyhop` أو `SHOP2` — كلتاهما بايثون خالص، خفيفتان، ويمكن تشغيلهما داخل Termux دون تبعيات ثقيلة.

بنية المجال (Domain):

```text
domain(whatsapp) {
  task reply_to_message(sender, content)
    method reply_via_notification
      precond: notification_active(sender)
      subtasks: [ reply_notification(token, content) ]

    method reply_via_chat
      precond: app_foregrounded("whatsapp")
      subtasks: [
        observe(target="whatsapp_chat", depth="focused"),
        set_text(node=find_input_field(), text=content),
        tap(node=find_send_button())
      ]

    method reply_via_deeplink
      precond: has_phone_number(sender)
      subtasks: [
        open_deep_link("whatsapp://send?phone=X&text=Y")
      ]
}
```

ترتيب الطرق (Methods) مهم: الأسرع والأكثر موثوقية أولاً. Notification > Deep Link > UI Automation.

#### حدود القرار

هذا القرار **لا** يُلزمنا بمكتبة HTN محددة. يُلزمنا بـ:

- وجود طبقة تخطيط غير LLM تعمل دون إنترنت.
- أن يكون مخرجها قابلاً للتحليل الثابت.
- أن يكون LLM Fallback لا Default.

#### معايير القبول لـ 0.2

- [ ] 20 سيناريو واتساب معياري تُنفَّذ بـ HTN دون LLM في < 200ms للخطة.
- [ ] 10 سيناريوهات غير معيارية تُنفَّذ بـ LLM + Validator.
- [ ] قياس نسبة HTN vs LLM في اختبار 100 مهمة: المتوقع 75–85% HTN.
- [ ] زمن فشل LLM (timeout) يجب أن يُفعّل fallback إلى "أبسط خطة ممكنة" لا إلى لا شيء.

---

### 0.3 القرار B — موقع حلقة الميتا (Meta-Loop Placement)

**السؤال:** هل يعيش التفكير التأملي (Reflection) داخل العملية الرئيسية أم منفصلاً؟

#### الخيارات

| المعيار | نفس العملية (async) | عملية منفصلة (مُقترح) | Thread منفصل |
| --- | --- | --- | --- |
| حماية الاستجابة الرئيسية | جزئية | كاملة | جزئية (GIL) |
| زمن وصول التفكير | < 10ms | < 50ms | < 30ms |
| تعقيد البناء | منخفض | متوسط | منخفض |
| عزل الأعطال | لا | نعم | لا |
| إمكانية الإيقاف القسري | صعبة | سهلة (SIGKILL) | صعبة |
| استهلاك الذاكرة | مشترك | مضاعف (~50MB) | مشترك |

#### القرار: عملية منفصلة

**عملية منفصلة**، ببروتوكول IPC صريح على Unix Domain Socket.

```text
┌──────────────────────────┐         ┌──────────────────────────┐
│   Main Agent Process     │◄───────►│   Reflector Process      │
│                          │  UDS    │                          │
│  - Event Dispatcher      │         │  - Reflection Policy     │
│  - Planner (HTN + LLM)   │         │  - Memory Query          │
│  - Bridge Client         │         │  - Plan Critique         │
│  - Memory Writer         │         │  - Meta-Prompts          │
└──────────────────────────┘         └──────────────────────────┘
         │                                      │
         │ Shared: SQLite (WAL)                 │
         └──────────────────────────────────────┘
```

#### لماذا عملية منفصلة رغم التكلفة؟

1. **لا تعطيل للاستجابة:** استدعاء LLM للتفكير قد يستغرق 5–30 ثانية. في نفس العملية، حتى مع asyncio، سيتنافس على CPU وذاكرة، وسيُبطئ استقبال أحداث Bridge.
2. **عزل الهلوسة:** إن هلوس Reflector وقرر حلقة لا نهائية، نُرسل له SIGKILL بدون التأثير على Main.
3. **نمذجة ذهنية أنظف:** التفكير = عملية. التنفيذ = عملية. الحدود واضحة.
4. **قابلية الملاحظة:** يمكن مراقبة Reflector بـ `strace` أو `py-spy` دون تشويش التنفيذ.

#### بروتوكول IPC — Unix Domain Socket

```text
Socket path: /data/data/com.termux/files/usr/var/run/agent-reflector.sock
Protocol:    JSONL (سطر = رسالة JSON)
```

رسائل من Main إلى Reflector:

```json
{"type": "reflect_request", "trigger": "stall", "context_snapshot_id": "snap_xyz"}
```

رسائل من Reflector إلى Main:

```text
{"type": "plan_revision", "original_plan_id": "p1", "revised_plan": [...], "reason": "..."}
```

**سياسة إعادة التشغيل:**

- Main يراقب Reflector بـ Heartbeat كل 5 ثوانٍ.
- فشل 3 مرات متتالية → Main ينتقل إلى **Degraded Mode**: تعطيل الميتا مؤقتاً، الاستمرار بالخطط HTN فقط.
- هذا يضمن ألا يُسقط موت Reflector الوكيل كاملاً.

#### معايير القبول لـ 0.3

- [ ] زمن استجابة Main لا يزيد عن 20% عند تشغيل Reflector (قياس مع/بدون).
- [ ] SIGKILL على Reflector لا يُسقط Main.
- [ ] Heartbeat يفشل 3 مرات → Degraded Mode خلال < 20 ثانية.
- [ ] Reflector crash loops لا تُستهلك أكثر من 5% CPU.

---

### 0.4 المحلل الثابت للخطط (Static Plan Analyzer)

**السؤال:** كيف نتأكد أن الخطة آمنة ومنطقية قبل تنفيذها؟

**المشكلة:** حتى مع HTN المقيّد، يمكن أن تُنتج خطة صحيحة نحوياً لكن كارثية دلالياً:

- خطة ترسل 50 رسالة في 10 ثوانٍ (Rate Limit).
- خطة تُرسل لشخص لم يراسله المستخدم من قبل (Social Risk).
- خطة تحوي `set_text` ثم `tap` على زر "حذف" بدل "إرسال" (Semantic Risk).
- خطة تكرر نفس الإجراء 30 مرة (Loop Detection).

**القرار:** طبقة تحليل قبل التنفيذ، لا بعد الفشل.

#### المعايير السبعة للمحلل

| # | المعيار | القاعدة | الإجراء عند الانتهاك |
| --- | --- | --- | --- |
| 1 | حد الطول | ≤ 15 إجراءً | رفض + طلب تبسيط |
| 2 | حد التكرار | لا نفس (verb, target) أكثر من 3 مرات | رفض + تشخيص Loop |
| 3 | حد المعدل | ≤ 5 إجراءات خطرة/دقيقة | تأجيل + إشعار |
| 4 | الترتيب الدلالي | `set_text` قبل `tap(send)` إجباري | رفض |
| 5 | تطابق السياق | كل `node_id` مذكور في آخر snapshot | رفض مع `STALE_PLAN` |
| 6 | المخاطر الاجتماعية | إجراء لجهة اتصال جديدة يتطلب Key C | رفض بدون توقيع |
| 7 | الحد المالي | أي إجراء مالي يحتاج حد صريح + Biometric | رفض |

#### مخطط المحلل

```python
def analyze(plan: List[Action], context: Context) -> AnalysisResult:
    checks = [
        check_length_limit,      # ≤ 15
        check_repetition,        # لا حلقة
        check_rate_limit,        # ≤ 5 dangerous/min
        check_semantic_order,    # نص قبل إرسال
        check_context_freshness, # لا node_id قديم
        check_social_risk,       # جديد ⇒ Key C
        check_financial_risk,    # مالي ⇒ Biometric
    ]
    for check in checks:
        result = check(plan, context)
        if not result.passed:
            return AnalysisResult.rejected(result.reason)
    return AnalysisResult.approved(plan)
```

**قاعدة الحسم:** المحلل **لا يُعدّل** الخطة، بل يرفضها. التعديل مهمة Planner، لا Analyzer. هذا يمنع "الإصلاح الصامت" الذي يخفي مشاكل التخطيط.

#### مثال تطبيقي — كشف Loop خفي

خطة LLM تُنتج:

```text
tap(node_A), observe, tap(node_A), observe, tap(node_A), observe, ...
```

المحلل:

- `check_repetition` يكتشف `tap(node_A)` تكرر 3 مرات → رفض.
- السبب يُرسل لـ Reflector: "خطة فيها حلقة، المهمة تتطلب إعادة تخطيط".
- Reflector يستدعي LLM بصيغة "الخطة السابقة فشلت بسبب X، اقترح بديلاً".

هذا هو الجسر بين المحلل الثابت والميتا-لوب. بدونهما، LLM يعيد نفس الخطأ إلى ما لا نهاية.

#### معايير القبول لـ 0.4

- [ ] 30 خطة اختبارية: 15 صحيحة تمر، 15 خاطئة تُرفض بسبب صحيح.
- [ ] زمن التحليل < 5ms لكل خطة من 15 إجراءً.
- [ ] كل رفض يُنتج `reason` قابلاً للترجمة إلى English/Arabic.
- [ ] لا يوجد "تعديل صامت" — كل تعديل يمر عبر Planner.

---

### ملخص Section 0

| # | القرار | الحالة |
| --- | --- | --- |
| 0.1 | مفردات إجراءات مغلقة (~15 verb) | ✅ مُثبَّت |
| 0.2 | Planner هجين: HTN أولاً، LLM fallback | ✅ مُثبَّت |
| 0.3 | Meta-Loop في عملية منفصلة عبر UDS | ✅ مُثبَّت |
| 0.4 | محلل ثابت للخطط قبل التنفيذ | ✅ مُثبَّت |

**التزام صريح:** أي تعديل على أحد هذه القرارات بعد Sprint 2 يستوجب RFC مكتوب يُراجع في PR منفصل. هذا ليس إجراءً شكلياً — هي حماية من الانحلال المعماري (Architectural Drift).
