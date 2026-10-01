# وكيل الذكاء الاصطناعي المستقل على أندرويد

وثائق التصميم المعماري لوكيل ذكاء اصطناعي يعمل على أندرويد عبر هيكلية هجينة: تطبيق جسر (Kotlin) وعقل وكيل (Python على Termux).

## الوثائق

0. [الهدف ونطاق المشروع](00-goal.md)
   - [مواصفة MVP-1: وضع الظل والتدريب](mvp-1-spec.md)
1. [الطبقة الأولى: تطبيق الجسر وتطويرات الإنتاج](01-bridge-app-architecture.md)
2. [الطبقة الثانية: عقل الوكيل (Python Agent Brain)](02-python-agent-brain.md) — مسودة

## الشيفرة

- [`agent/agent.py`](agent/agent.py): هيكل Python Agent لـ Sprint 0 (عقد التشغيل في Section 1).
- الاختبارات: `python3 -m unittest discover -s android-agent/agent/tests -v`

## أعمال معلّقة

- Layer 1 تحتاج PR لاحقاً لتوحيد عقد البيانات (`action`/`params`/`locator`) وحذف `command`/`target_node_id`/`min_version_required` من طلب التنفيذ. يبقى `node_id` في رد الـ snapshot فقط، ولا يعود Python يرسله في الطلبات. ويُضاف `screen_instance` إلى رد الـ snapshot (انظر 0.5.3).
- **قيد تشغيلي:** App Profiles تُكتب وتُحدَّث يدوياً، بإصدار، وتُشحن داخل APK الـ Bridge فيغطيها توقيعه. لا يعدّلها Python ولا يقترح تعديلها؛ عند فشل حل محدد يُبلّغ الوكيل ولا يُعدّل. إن فُصلت يوماً عن الـ APK، تحتاج توقيعاً مستقلاً من مؤلفها. التفاصيل في Section 11 من الطبقة الثانية.
- **الاقتران:** حُسم للـ MVP في [mvp-1-spec](mvp-1-spec.md) (سر مشترك واحد ومصافحة متبادلة). تسلسل المفاتيح الكامل في 0.6 ما زال يحتاج آلية اقتران خاصة به قبل Section 2.
