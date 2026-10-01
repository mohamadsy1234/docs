# وكيل الذكاء الاصطناعي المستقل على أندرويد

وثائق التصميم المعماري لوكيل ذكاء اصطناعي يعمل على أندرويد عبر هيكلية هجينة: تطبيق جسر (Kotlin) وعقل وكيل (Python على Termux).

## الوثائق

1. [الطبقة الأولى: تطبيق الجسر وتطويرات الإنتاج](01-bridge-app-architecture.md)
2. [الطبقة الثانية: عقل الوكيل (Python Agent Brain)](02-python-agent-brain.md) — مسودة

## أعمال معلّقة

- Layer 1 تحتاج PR لاحقاً لتوحيد عقد البيانات (`action`/`params`/`locator`) وحذف `command`/`target_node_id`/`min_version_required` من طلب التنفيذ. يبقى `node_id` في رد الـ snapshot فقط، ولا يعود Python يرسله في الطلبات. ويُضاف `screen_instance` إلى رد الـ snapshot (انظر 0.5.3).
- **Blocker:** Section 2 (Bridge Client) لا يبدأ قبل حسم آلية الاقتران الأولى، أي كيف يصل المفتاح B إلى Termux أول مرة بشكل آمن (انظر 0.6).
