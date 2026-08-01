from fastapi import FastAPI, Request, Header, HTTPException, BackgroundTasksimport httpx
import os
import io
import re
import logging
import pdfplumber
import docx
from docx import Document
from openai import OpenAI

from app.config import BOT_TOKEN, ADMIN_CHAT_ID, WEBHOOK_SECRET, BASE_URL, PERPLEXITY_API_KEY
from app.db import init_db, get_or_create_user, update_user, get_user_state_full, set_user_state

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

app = FastAPI()

ai_client = OpenAI(api_key=PERPLEXITY_API_KEY, base_url="https://api.perplexity.ai/v1")


@app.on_event("startup")
async def startup_event():
    init_db()
    logger.info("Database initialized, bot started")
    if BASE_URL:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
                    json={"url": f"{BASE_URL}/webhook", "secret_token": WEBHOOK_SECRET}
                )
                logger.info(f"Webhook auto-registration: {response.status_code} {response.text}")
        except Exception:
            logger.exception("Failed to auto-register webhook")


def main_menu():
    return {"inline_keyboard": [
        [{"text": "🚀 ابدأ إعداد الملف الوظيفي", "callback_data": "setup_profile"}],
        [{"text": "📄 رفع سيرتي الذاتية", "callback_data": "upload_cv"}],
        [{"text": "✉️ توليد خطاب تقديم (كفر ليتر)", "callback_data": "gen_cover_letter"}],
        [{"text": "❓ مساعدة", "callback_data": "help"}]
    ]}


def language_menu():
    return {"inline_keyboard": [
        [{"text": "🇸🇦 العربية", "callback_data": "lang_ar"}],
        [{"text": "🇬🇧 English", "callback_data": "lang_en"}]
    ]}


async def send_telegram_message(chat_id, text, reply_markup=None, parse_mode=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=payload)
            if response.status_code != 200:
                logger.warning(f"sendMessage failed: {response.status_code} {response.text}")
    except httpx.RequestError:
        logger.exception("Network error in send_telegram_message")


async def send_long_message(chat_id, text, reply_markup=None, parse_mode=None):
    max_len = 4000
    if len(text) <= max_len:
        await send_telegram_message(chat_id, text, reply_markup, parse_mode)
        return
    parts = [text[i:i+max_len] for i in range(0, len(text), max_len)]
    for i, part in enumerate(parts):
        is_last = (i == len(parts) - 1)
        await send_telegram_message(chat_id, part, reply_markup if is_last else None, parse_mode if is_last else None)


async def answer_callback_query(callback_query_id, text=""):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery",
                json={"callback_query_id": callback_query_id, "text": text}
            )
    except httpx.RequestError:
        logger.exception("Network error in answer_callback_query")


async def send_document_bytes(chat_id, file_bytes, filename, caption=""):
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            files = {"document": (filename, file_bytes)}
            data = {"chat_id": chat_id, "caption": caption}
            response = await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument",
                data=data, files=files
            )
            if response.status_code != 200:
                logger.warning(f"sendDocument failed: {response.status_code} {response.text}")
    except httpx.RequestError:
        logger.exception("Network error in send_document_bytes")


async def download_telegram_file(file_id: str) -> bytes:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getFile", params={"file_id": file_id})
        file_path = resp.json()["result"]["file_path"]
        file_resp = await client.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}")
        return file_resp.content


def extract_text_from_cv(file_bytes: bytes, file_name: str) -> str:
    text = ""
    try:
        if file_name.lower().endswith(".pdf"):
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
        elif file_name.lower().endswith((".docx", ".doc")):
            doc = docx.Document(io.BytesIO(file_bytes))
            text = "\n".join(p.text for p in doc.paragraphs)
    except Exception:
        logger.exception("Failed to extract text from CV")
    return text.strip()


def build_docx_from_text(text: str) -> bytes:
    doc = Document()
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.endswith(":") or line.isupper():
            doc.add_heading(line, level=2)
        else:
            doc.add_paragraph(line)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


async def ask_ai(prompt: str) -> str:
    try:
        response = ai_client.responses.create(model="openai/gpt-5-mini", input=prompt)
        return response.output_text.strip()
    except Exception:
        logger.exception("AI call failed")
        return ""


def extract_score(analysis_text: str) -> int:
    match = re.search(r"(\d{1,3})\s*(?:/|من)\s*100", analysis_text)
    if match:
        return int(match.group(1))
    match2 = re.search(r"\b(\d{1,3})\b", analysis_text)
    return int(match2.group(1)) if match2 else 0


async def analyze_cv(text: str, language: str = "العربية") -> str:
    prompt = f"""حلل السيرة الذاتية التالية من ناحية توافقها مع أنظمة ATS، بدون اختلاق معلومات غير موجودة في النص.
اكتب الرد كاملًا بلغة: {language}

السيرة الذاتية:
{text}

أعطني:
1. تقييم عام من 100
2. نقاط القوة (3 نقاط)
3. نقاط الضعف (3 نقاط)
4. اقتراحات تحسين محددة"""
    return await ask_ai(prompt)


async def rewrite_cv_ats(text: str, language: str = "العربية") -> str:
    prompt = f"""أعد كتابة السيرة الذاتية التالية بحيث تكون مُحسّنة بالكامل لأنظمة ATS (Applicant Tracking System)، مع الحفاظ الصارم على كل الحقائق والمعلومات الموجودة في النص الأصلي فقط، بدون اختلاق أي خبرة أو مهارة أو تاريخ غير مذكور أصلًا.
اكتب السيرة الذاتية النهائية بالكامل بلغة: {language}

معايير التحسين:
- عناوين أقسام واضحة (الملخص المهني، الخبرات، المهارات، التعليم)
- صيغة نظيفة بدون جداول أو رموز معقدة
- كلمات مفتاحية مهنية قوية مرتبطة بمجال العمل
- ترتيب منطقي وتنسيق بسيط يقرأه أي نظام ATS بسهولة

السيرة الذاتية الأصلية:
{text}

أعطني فقط النص النهائي المُحسّن باللغة المطلوبة، بدون أي شرح إضافي قبله أو بعده."""
    return await ask_ai(prompt)


async def generate_cover_letter(cv_text: str, job_description: str, language: str = "العربية") -> str:
    prompt = f"""بناءً على السيرة الذاتية التالية والوصف الوظيفي المطلوب، اكتب خطاب تقديم (Cover Letter) مهني ومخصص، معتمدًا فقط على المعلومات الموجودة فعليًا في السيرة الذاتية بدون اختلاق أي معلومة.
اكتب الخطاب بالكامل بلغة: {language}

السيرة الذاتية:
{cv_text}

الوصف الوظيفي المطلوب:
{job_description}

اكتب خطاب تقديم قصير (200-300 كلمة) يربط بين مؤهلات المرشح الحقيقية ومتطلبات الوظيفة."""
    return await ask_ai(prompt)


@app.get("/")
async def root():
    return {"status": "ok"}


@app.post("/webhook")
async def telegram_webhook(request: Request, x_telegram_bot_api_secret_token: str = Header(None)):
    if x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        update = await request.json()
    except Exception:
        return {"ok": True}

    try:
        callback_query = update.get("callback_query")
        if callback_query:
            return await handle_callback_query(callback_query)
        message = update.get("message")
        if message:
            return await handle_message(message)
        return {"ok": True}
    except Exception:
        logger.exception(f"Unhandled error processing update: {update}")
        return {"ok": True}


async def handle_callback_query(callback_query):
    callback_id = callback_query["id"]
    data = callback_query.get("data", "")
    chat_id = callback_query.get("message", {}).get("chat", {}).get("id")
    if not chat_id:
        return {"ok": True}

    if data == "setup_profile":
        set_user_state(chat_id, "waiting_full_name")
        await answer_callback_query(callback_id, "إعداد الملف")
        await send_telegram_message(chat_id, "👋 لنبدأ إعداد ملفك الوظيفي.\n\nأرسل اسمك الكامل:")
        return {"ok": True}

    if data == "upload_cv":
        set_user_state(chat_id, "waiting_language")
        await answer_callback_query(callback_id, "اختر اللغة")
        await send_telegram_message(chat_id, "🌐 بأي لغة تريد سيرتك الذاتية المحسّنة وخطاب التقديم؟", language_menu())
        return {"ok": True}

    if data in ("lang_ar", "lang_en"):
        chosen_lang = "العربية" if data == "lang_ar" else "English"
        update_user(chat_id, cv_language=chosen_lang)
        set_user_state(chat_id, "waiting_cv")
        await answer_callback_query(callback_id, chosen_lang)
        await send_telegram_message(chat_id, "📄 أرسل سيرتك الذاتية الآن كملف PDF أو DOCX:")
        return {"ok": True}

    if data == "gen_cover_letter":
        user = get_or_create_user(chat_id)
        if not user.get("cv_file_id"):
            await answer_callback_query(callback_id, "لا توجد سيرة ذاتية")
            await send_telegram_message(chat_id, "⚠️ يجب رفع سيرتك الذاتية أولًا قبل توليد خطاب التقديم.", main_menu())
            return {"ok": True}
        set_user_state(chat_id, "waiting_job_description")
        await answer_callback_query(callback_id, "خطاب التقديم")
        await send_telegram_message(chat_id, "✉️ أرسل الآن الوصف الوظيفي (Job Description) للوظيفة المطلوبة:")
        return {"ok": True}

    if data == "help":
        await answer_callback_query(callback_id, "مساعدة")
        await send_telegram_message(
            chat_id,
            "🤖 خطوات استخدام البوت:\n\n"
            "1️⃣ اضغط 'ابدأ إعداد الملف الوظيفي' وأدخل بياناتك\n"
            "2️⃣ اختر لغة السيرة الذاتية (عربي/إنجليزي)\n"
            "3️⃣ ارفع سيرتك الذاتية (PDF أو DOCX)\n"
            "4️⃣ سيحللها الذكاء الاصطناعي ويرسل لك نسخة محسّنة بنظام ATS\n"
            "5️⃣ أرسل الوصف الوظيفي المطلوب لتوليد خطاب تقديم مخصص تلقائيًا",
            main_menu()
        )
        return {"ok": True}

    return {"ok": True}


async def handle_message(message):
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    if not chat_id:
        return {"ok": True}

    text = (message.get("text") or "").strip()
    user = get_or_create_user(chat_id)

    if text == "/start":
        set_user_state(chat_id, "")
        await send_telegram_message(
            chat_id,
            "أهلًا بك 👋\n\nأنا بوت تحليل وتحسين السيرة الذاتية بنظام ATS.\n\nابدأ الآن:",
            main_menu()
        )
        return {"ok": True}

    if text == "/cancel":
        set_user_state(chat_id, "")
        await send_telegram_message(chat_id, "❌ تم إلغاء العملية الحالية.", main_menu())
        return {"ok": True}

    current_state, _ = get_user_state_full(chat_id)

    if current_state == "waiting_full_name":
        if len(text) < 3:
            await send_telegram_message(chat_id, "⚠️ أرسل اسمًا صحيحًا (3 أحرف على الأقل).")
            return {"ok": True}
        update_user(chat_id, full_name=text)
        set_user_state(chat_id, "waiting_phone")
        await send_telegram_message(chat_id, "📱 أرسل رقم جوالك:")
        return {"ok": True}

    if current_state == "waiting_phone":
        digits_only = text.replace(" ", "").replace("-", "")
        if not digits_only.isdigit() or len(digits_only) < 9:
            await send_telegram_message(chat_id, "⚠️ أرسل رقم جوال صحيح (أرقام فقط).")
            return {"ok": True}
        update_user(chat_id, phone=text)
        set_user_state(chat_id, "waiting_specialization")
        await send_telegram_message(chat_id, "🎯 أرسل تخصصك أو مجالك المستهدف:")
        return {"ok": True}

    if current_state == "waiting_specialization":
        if len(text) < 2:
            await send_telegram_message(chat_id, "⚠️ أرسل تخصصًا صحيحًا.")
            return {"ok": True}
        update_user(chat_id, specialization=text)
        set_user_state(chat_id, "waiting_language")
        await send_telegram_message(chat_id, "✅ تم حفظ بياناتك.\n\n🌐 بأي لغة تريد سيرتك الذاتية المحسّنة وخطاب التقديم؟", language_menu())
        return {"ok": True}

    if current_state == "waiting_language":
        await send_telegram_message(chat_id, "⚠️ الرجاء استخدام الأزرار لاختيار اللغة.", language_menu())
        return {"ok": True}

    if "document" in message:
        document = message["document"]
        file_id = document.get("file_id", "")
        file_name = document.get("file_name", "cv_file")

        if not file_name.lower().endswith((".pdf", ".docx", ".doc")):
            await send_telegram_message(chat_id, "⚠️ الرجاء رفع ملف بصيغة PDF أو DOCX فقط.")
            return {"ok": True}

        if current_state != "waiting_cv":
            await send_telegram_message(chat_id, "⚠️ اضغط '📄 رفع سيرتي الذاتية' أولًا من القائمة.", main_menu())
            return {"ok": True}

        await send_telegram_message(chat_id, "⏳ جاري تحليل سيرتك الذاتية، الرجاء الانتظار...")

        file_bytes = await download_telegram_file(file_id)
        cv_text = extract_text_from_cv(file_bytes, file_name)

        if len(cv_text.strip()) < 30:
            await send_telegram_message(chat_id, "⚠️ تعذر استخراج نص كافٍ من الملف. تأكد أن الملف يحتوي نصًا قابلًا للقراءة.")
            set_user_state(chat_id, "")
            return {"ok": True}

        update_user(chat_id, cv_file_id=file_id, cv_file_name=file_name, cv_text=cv_text)

        cv_language = user.get("cv_language", "العربية")

        analysis = await analyze_cv(cv_text, cv_language)
        await send_long_message(chat_id, f"📊 نتيجة التحليل الأولي:\n\n{analysis}")

        current_text = cv_text
        final_score = extract_score(analysis)
        max_attempts = 5
        attempt = 0

        improved_text = await rewrite_cv_ats(current_text, cv_language)
        while improved_text and attempt < max_attempts:
            re_analysis = await analyze_cv(improved_text, cv_language)
            new_score = extract_score(re_analysis)
            if new_score >= final_score:
                current_text = improved_text
                final_score = new_score
            attempt += 1
            if final_score >= 95:
                break
            improved_text = await rewrite_cv_ats(current_text, cv_language)

        if current_text != cv_text:
            update_user(chat_id, cv_text=current_text)
            docx_bytes = build_docx_from_text(current_text)
            await send_document_bytes(
                chat_id, docx_bytes, "CV_Improved_ATS.docx",
                caption=f"✅ نسخة محسّنة نهائية (تقييم ATS التقديري: {final_score}/100)"
            )
        else:
            await send_telegram_message(chat_id, "⚠️ تعذر تحسين السيرة الذاتية تلقائيًا، حاول مرة أخرى.")

        set_user_state(chat_id, "")
        await send_telegram_message(
            chat_id,
            "🎉 يمكنك الآن توليد خطاب تقديم مخصص عن طريق زر '✉️ توليد خطاب تقديم' وإرسال الوصف الوظيفي.",
            main_menu()
        )

        if ADMIN_CHAT_ID:
            await send_telegram_message(
                int(ADMIN_CHAT_ID),
                f"📥 عميل جديد حلل سيرته:\n👤 {user.get('full_name', 'غير محدد')}\n📱 {user.get('phone', 'غير محدد')}\n🆔 {chat_id}"
            )

        return {"ok": True}

    if current_state == "waiting_job_description":
        if len(text) < 10:
            await send_telegram_message(chat_id, "⚠️ أرسل وصفًا وظيفيًا أكثر تفصيلًا.")
            return {"ok": True}

        user = get_or_create_user(chat_id)
        cv_text = user.get("cv_text", "")
        if not cv_text:
            await send_telegram_message(chat_id, "⚠️ لم يتم العثور على سيرتك الذاتية. ارفعها من جديد.", main_menu())
            set_user_state(chat_id, "")
            return {"ok": True}

        cv_language = user.get("cv_language", "العربية")

        await send_telegram_message(chat_id, "⏳ جاري توليد خطاب التقديم...")
        cover_letter = await generate_cover_letter(cv_text, text, cv_language)
        set_user_state(chat_id, "")
        await send_long_message(chat_id, f"✉️ خطاب التقديم المخصص:\n\n{cover_letter}", main_menu())
        return {"ok": True}

    await send_telegram_message(chat_id, "لم أفهم طلبك، استخدم الأزرار أدناه:", main_menu())
    return {"ok": True}
