# إضافة مساعد Gemini الداخلي

## الملفات
- `page.tsx`: نسخة الواجهة بعد إضافة تبويب AI Assistant إلى الصفحة التي أرسلتها.
- `ai_assistant.py`: مسار FastAPI مستقل للمحادثة الداخلية.

## 1) استبدال الواجهة
انسخ `page.tsx` إلى:
`apps/web/app/page.tsx`

## 2) إضافة مسار API
انسخ `ai_assistant.py` إلى:
`apps/api/app/api/routes/ai_assistant.py`

ثم افتح `apps/api/app/main.py` وأضف مع بقية استيرادات المسارات:
```python
from app.api.routes.ai_assistant import router as ai_assistant_router
```
وأضف مع بقية `app.include_router(...)`:
```python
app.include_router(ai_assistant_router, prefix=settings.api_v1_prefix)
```

## 3) تحقق آمن
لا تغيّر `AI_AUTO_REPLY_ENABLED=false` ولا `AI_REQUIRE_HUMAN_APPROVAL=true`.
لا تستخدم زر Approve & Send للاختبار. التبويب الجديد يستدعي فقط:
`POST /api/v1/organizations/{organization_id}/ai-assistant/chat`
ولا يستدعي أي endpoint للإرسال أو الحجز.

## 4) إعادة تشغيل الخدمات
بعد نسخ الملفات، أعد تشغيل خدمات API والويب بالطريقة المعتادة في بيئتك. لا تحذف volumes ولا تعِد إنشاء قاعدة البيانات.
