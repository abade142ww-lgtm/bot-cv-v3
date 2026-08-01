from fastapi import FastAPI, Request, Header, HTTPException
import httpx
import csv
import os
import html
import logging
from datetime import datetime
import re
import pdfplumber
import docx
import io

from app.config import BOT_TOKEN, ADMIN_CHAT_ID, WEBHOOK_SECRET, BASE_URL
from app.db import (
    init_db, get_or_create_user, update_user,
    get_user_state, get_user_state_full, set_user_state,
    save_application, get_user_requests
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

app = FastAPI()

TRAINING_FILE = "تدريب_الجهات.csv"
_TRAINING_CACHE = None


@app.on_event("startup")
async def startup_event():
    init_db()
    logger.info("Database initialized, bot started")

    if BASE_URL:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
                    json={
                        "url": f"{BASE_URL}/webhook",
                        "secret_token": WEBHOOK_SECRET
                    }
                )
                logger.info(f"Webhook auto-registration: {response.status_code} {response.text}")
        except Exception:
            logger.exception("Failed to auto-register webhook")


REGION_CITIES = {
    "riyadh": {"label": "الرياض", "emoji": "🏛️", "cities": ["الرياض", "الخرج", "الدوادمي", "شقراء", "المجمعة", "الزلفي", "وادي الدواسر", "الأفلاج"]},
    "makkah": {"label": "مكة المكرمة", "emoji": "🕋", "cities": ["مكة المكرمة", "جدة", "الطائف", "رابغ", "القنفذة", "الليث", "الجموم", "خليص"]},
    "madinah": {"label": "المدينة المنورة", "emoji": "🕌", "cities": ["المدينة المنورة", "ينبع", "العلا", "خيبر", "بدر", "مهد الذهب", "السويرقية", "الفريش"]},
    "eastern": {"label": "المنطقة الشرقية", "emoji": "🏖️", "cities": ["الدمام", "الخبر", "الظهران", "الجبيل", "الأحساء", "حفر الباطن", "القطيف", "رأس تنورة"]},
    "asir": {"label": "عسير", "emoji": "⛰️", "cities": ["أبها", "خميس مشيط", "بيشة", "محايل عسير", "النماص", "تنومة", "ظهران الجنوب", "رجال ألمع"]},
    "tabuk": {"label": "تبوك", "emoji": "🏔️", "cities": ["تبوك", "ضباء", "الوجه", "أملج", "تيماء", "حقل", "البدع"]},
    "qassim": {"label": "القصيم", "emoji": "🌾", "cities": ["بريدة", "عنيزة", "الرس", "البكيرية", "المذنب", "البدائع", "رياض الخبراء"]},
    "hail": {"label": "حائل", "emoji": "🌅", "cities": ["حائل", "بقعاء", "الشنان", "الغزالة", "سميراء", "موقق"]},
    "north": {"label": "الحدود الشمالية", "emoji": "🌵", "cities": ["عرعر", "رفحاء", "طريف", "العويقيلة"]},
    "jazan": {"label": "جازان", "emoji": "🌴", "cities": ["جازان", "صبيا", "أبو عريش", "صامطة", "بيش", "الدرب", "العارضة"]},
    "najran": {"label": "نجران", "emoji": "🖼️", "cities": ["نجران", "شرورة", "حبونا", "بدر الجنوب", "يدمة"]},
    "baha": {"label": "الباحة", "emoji": "🌲", "cities": ["الباحة", "بلجرشي", "المخواة", "العقيق", "قلوة", "المندق"]},
    "jouf": {"label": "الجوف", "emoji": "⛺", "cities": ["سكاكا", "دومة الجندل", "القريات", "طبرجل", "صوير"]}
}

QUALIFICATIONS = ["دكتوراه", "ماجستير", "بكالوريوس", "دبلوم", "ثانوية"]
GENDERS = [("male", "ذكر 👨"), ("female", "أنثى 👩")]


def load_training_contacts():
    global _TRAINING_CACHE
    if _TRAINING_CACHE is not None:
        return _TRAINING_CACHE
    if not os.path.exists(TRAINING_FILE):
        _TRAINING_CACHE = []
        return _TRAINING_CACHE
    rows = []
    try:
        with open(TRAINING_FILE, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
    except Exception:
        logger.exception("Failed to load training CSV")
        rows = []
    _TRAINING_CACHE = rows
    return rows


def normalize_text(value):
    if not value:
        return ""
    return str(value).strip()


def split_city_values(city_text):
    if not city_text:
        return []
    separators = [" - ", " – ", "-", "–", "،", ","]
    temp = str(city_text)
    for sep in separators:
        temp = temp.replace(sep, "|")
    return [part.strip() for part in temp.split("|") if part.strip()]


def get_training_contacts():
    rows = load_training_contacts()
    return [r for r in rows if normalize_text(r.get("service_tag", "")).lower() == "training"]


def filter_training_contacts_by_selected_cities(selected_cities):
    contacts = get_training_contacts()
    if not selected_cities:
        return contacts
    selected_set = {normalize_text(c) for c in selected_cities if normalize_text(c)}
    filtered = []
    for row in contacts:
        row_cities = split_city_values(normalize_text(row.get("city", "")))
        row_city_set = {normalize_text(c) for c in row_cities if normalize_text(c)}
        if selected_set.intersection(row_city_set):
            filtered.append(row)
    return filtered


def get_training_stats_for_user(user):
    selected_cities = user.get("selected_cities", [])
    contacts = filter_training_contacts_by_selected_cities(selected_cities)
    email_count = len([c for c in contacts if normalize_text(c.get("contact_type", "")).lower() == "email"])
    website_count = len([c for c in contacts if normalize_text(c.get("contact_type", "")).lower() == "website"])
    unknown_count = len([c for c in contacts if normalize_text(c.get("contact_type", "")).lower() == "unknown"])
    return {
        "total": len(contacts),
        "email_count": email_count,
        "website_count": website_count,
        "unknown_count": unknown_count,
        "contacts": contacts
    }


def escape_html_text(value):
    if value is None:
        return ""
    return html.escape(str(value))


def build_training_preview_html(user, limit=5):
    stats = get_training_stats_for_user(user)
    preview_contacts = stats["contacts"][:limit]
    selected_cities = user.get("selected_cities", [])
    selected_cities_text = "، ".join(selected_cities) if selected_cities else "غير محدد"

    text = (
        "<b>🎓 جهات التدريب المطابقة لمدنك</b>\n\n"
        f"<b>📍 المدن المختارة:</b> {escape_html_text(selected_cities_text)}\n"
        f"<b>📊 إجمالي الجهات:</b> {stats['total']}\n"
        f"- بريد إلكتروني: {stats['email_count']}\n"
        f"- عبر الموقع: {stats['website_count']}\n"
        f"- غير محدد: {stats['unknown_count']}\n"
    )

    if preview_contacts:
        text += "\n\n<b>📋 أمثلة على الجهات:</b>\n"
        for item in preview_contacts:
            name = escape_html_text(item.get("name", "بدون اسم"))
            city = escape_html_text(item.get("city", "غير محدد"))
            contact_type = escape_html_text(item.get("contact_type", "غير محدد"))
            contact_value = escape_html_text(item.get("contact_value", "غير محدد"))
            text += (
                f"\n• <b>{name}</b>\n"
                f"المدينة: {city}\n"
                f"النوع: {contact_type}\n"
                f"التواصل: <code>{contact_value}</code>\n"
            )
    else:
        text += "\n\nلا توجد جهات مطابقة حاليًا للمدن المختارة."

    return text


def main_menu():
    return {"inline_keyboard": [
        [{"text": "🚀 ابدأ إعداد الملف", "callback_data": "start_profile_setup"}],
        [{"text": "📁 ملفي الوظيفي", "callback_data": "profile"}],
        [{"text": "⚙️ تفضيلاتي", "callback_data": "preferences"}],
        [{"text": "📄 سيرتي الذاتية", "callback_data": "cv_menu"}],
        [{"text": "🚀 الخدمات", "callback_data": "services"}],
        [{"text": "📦 طلباتي السابقة", "callback_data": "my_requests"}],
        [{"text": "💳 الباقات", "callback_data": "plans"}],
        [{"text": "💬 الدعم", "callback_data": "support"}]
    ]}


def preferences_menu():
    return {"inline_keyboard": [
        [{"text": "🎯 التخصصات", "callback_data": "pref_specialization"}],
        [{"text": "👤 الاسم الكامل", "callback_data": "pref_full_name"}],
        [{"text": "📱 رقم الجوال", "callback_data": "pref_phone"}],
        [{"text": "📄 رفع السيرة الذاتية", "callback_data": "pref_cv"}],
        [{"text": "⬅️ رجوع", "callback_data": "back_main"}]
    ]}


def cv_menu():
    return {"inline_keyboard": [
        [{"text": "📤 رفع / تحديث السيرة الذاتية", "callback_data": "upload_cv"}],
        [{"text": "⬅️ رجوع", "callback_data": "back_main"}]
    ]}


def services_menu():
    return {"inline_keyboard": [
        [{"text": "🎓 تقديم تدريب تعاوني", "callback_data": "service_training"}],
        [{"text": "🏢 إرسال للشركات السعودية", "callback_data": "service_companies"}],
        [{"text": "🛡️ إرسال لجهات السايبر", "callback_data": "service_cyber"}],
        [{"text": "✨ تصميم السيرة الذاتية", "callback_data": "service_cv_design"}],
        [{"text": "⬅️ رجوع", "callback_data": "back_main"}]
    ]}


def plans_menu():
    return {"inline_keyboard": [
        [{"text": "🆓 تجريبي", "callback_data": "plan_trial"}],
        [{"text": "📅 شهري", "callback_data": "plan_monthly"}],
        [{"text": "🗓️ نصف سنوي", "callback_data": "plan_halfyear"}],
        [{"text": "📆 سنوي", "callback_data": "plan_yearly"}],
        [{"text": "⬅️ رجوع", "callback_data": "back_main"}]
    ]}


def region_menu():
    keyboard = [[{"text": f"{v['emoji']} {v['label']}", "callback_data": f"region:{k}"}] for k, v in REGION_CITIES.items()]
    keyboard.append([{"text": "⬅️ رجوع", "callback_data": "back_main"}])
    return {"inline_keyboard": keyboard}


def cities_multi_menu(user):
    region_key = user.get("region_key", "")
    selected = set(user.get("selected_cities", []))
    if not region_key or region_key not in REGION_CITIES:
        return {"inline_keyboard": [[{"text": "⬅️ رجوع", "callback_data": "start_profile_setup"}]]}

    region = REGION_CITIES[region_key]
    keyboard = []

    if selected:
        keyboard.append([{"text": "--- المختارة من هذه المنطقة ---", "callback_data": "noop"}])
        for city in region["cities"]:
            if city in selected:
                keyboard.append([{"text": f"{city} ✅", "callback_data": f"toggle_city:{city}"}])

    for city in region["cities"]:
        if city not in selected:
            keyboard.append([{"text": city, "callback_data": f"toggle_city:{city}"}])

    keyboard.append([
        {"text": f"تم ({len(selected)} مدينة)", "callback_data": "cities_done"},
        {"text": "رجوع", "callback_data": "start_profile_setup"}
    ])
    return {"inline_keyboard": keyboard}


def qualification_menu():
    keyboard = [[{"text": q, "callback_data": f"qualification:{q}"}] for q in QUALIFICATIONS]
    keyboard.append([{"text": "رجوع", "callback_data": "back_to_cities"}])
    return {"inline_keyboard": keyboard}


def gender_menu():
    keyboard = [[{"text": label, "callback_data": f"gender:{key}"}] for key, label in GENDERS]
    keyboard.append([{"text": "رجوع", "callback_data": "back_to_qualification"}])
    return {"inline_keyboard": keyboard}


def services_after_setup_menu():
    return {"inline_keyboard": [
        [{"text": "🎯 فتح تفضيلاتي", "callback_data": "preferences"}],
        [{"text": "🎓 التدريب التعاوني", "callback_data": "service_training"}],
        [{"text": "🏢 الشركات السعودية", "callback_data": "service_companies"}],
        [{"text": "📄 رفع السيرة الذاتية", "callback_data": "pref_cv"}],
        [{"text": "⬅️ القائمة الرئيسية", "callback_data": "back_main"}]
    ]}


def training_preview_menu():
    return {"inline_keyboard": [
        [{"text": "✍️ ابدأ التقديم", "callback_data": "training_apply"}],
        [{"text": "⚙️ تعديل المدن", "callback_data": "start_profile_setup"}],
        [{"text": "⬅️ رجوع", "callback_data": "services"}]
    ]}


def profile_summary(user):
    cities = "، ".join(user["selected_cities"]) if user["selected_cities"] else "غير محدد"
    return (
        "✅ تم إعداد ملفك الوظيفي\n\n"
        f"🌍 المنطقة: {user['region_label'] or 'غير محدد'}\n"
        f"📍 المدن: {cities}\n"
        f"🎓 المؤهل: {user['qualification'] or 'غير محدد'}\n"
        f"🧑 الجنس: {user['gender'] or 'غير محدد'}\n"
        f"🎯 التخصص: {user['specialization'] or 'غير محدد'}\n"
        f"👤 الاسم الكامل: {user['full_name'] or 'غير محدد'}\n"
        f"📱 الجوال: {user['phone'] or 'غير محدد'}\n"
        f"📄 السيرة الذاتية: {user['cv_file_name'] or 'لم يتم الرفع بعد'}"
    )


def setup_success_html(user):
    cities = "، ".join(user["selected_cities"]) if user["selected_cities"] else "غير محدد"
    return (
        "<b>✅ تم إعداد ملفك الوظيفي بنجاح!</b>\n\n"
        f"📍 <b>مدنك:</b> {escape_html_text(cities)}\n"
        f"🎓 <b>مؤهلك:</b> {escape_html_text(user['qualification'])}\n"
        f"🧑 <b>الجنس:</b> {escape_html_text(user['gender'])}\n\n"
        "🔔 سنساعدك على تخصيص فرصك بشكل أفضل.\n"
        "يمكنك الآن إكمال البيانات المتبقية من زر <b>فتح تفضيلاتي</b>."
    )


async def send_telegram_message(chat_id, text, reply_markup=None, parse_mode=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=payload)
            if response.status_code != 200:
                logger.warning(f"sendMessage failed: {response.status_code} {response.text}")
    except httpx.RequestError:
        logger.exception("Network error in send_telegram_message")


async def answer_callback_query(callback_query_id, text=""):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery",
                json={"callback_query_id": callback_query_id, "text": text}
            )
            if response.status_code != 200:
                logger.warning(f"answerCallbackQuery failed: {response.status_code} {response.text}")
    except httpx.RequestError:
        logger.exception("Network error in answer_callback_query")


async def notify_admin_application(application):
    if not ADMIN_CHAT_ID:
        return
    admin_message = (
        "📥 طلب جديد\n\n"
        f"نوع الخدمة: {application['service_type']}\n"
        f"الاسم: {application['full_name']}\n"
        f"المنطقة: {application['region_label']}\n"
        f"المدن: {application['selected_cities']}\n"
        f"التخصص: {application['specialization']}\n"
        f"المؤهل: {application['qualification']}\n"
        f"الجنس: {application['gender']}\n"
        f"الجوال: {application['phone']}\n"
        f"عنوان الرسالة: {application['email_subject']}\n"
        f"نص الرسالة: {application['email_body']}\n"
        f"ملف السيرة: {application['cv_file_name']}\n"
        f"جهات التدريب المتاحة: {application.get('training_total_contacts', 0)}\n"
        f"بريد إلكتروني: {application.get('training_email_contacts', 0)}\n"
        f"عبر الموقع: {application.get('training_website_contacts', 0)}\n"
        f"غير محدد: {application.get('training_unknown_contacts', 0)}\n"
        f"وقت الإرسال: {application['submitted_at']}"
    )
    await send_telegram_message(int(ADMIN_CHAT_ID), admin_message)


def can_submit_service(user):
    return all([
        user["region_label"], user["selected_cities"], user["qualification"],
        user["gender"], user["specialization"], user["full_name"],
        user["phone"], user["cv_file_name"]
    ])


async def start_service_flow(chat_id, user, service_type):
    update_user(chat_id, service_type=service_type)

    if not user["specialization"]:
        set_user_state(chat_id, "waiting_specialization", context="service")
        await send_telegram_message(chat_id, "قبل المتابعة، أرسل تخصصك أو مجالك المستهدف:\n\n(أرسل /cancel للإلغاء)")
        return
    if not user["full_name"]:
        set_user_state(chat_id, "waiting_full_name", context="service")
        await send_telegram_message(chat_id, "قبل المتابعة، أرسل اسمك الكامل:\n\n(أرسل /cancel للإلغاء)")
        return
    if not user["phone"]:
        set_user_state(chat_id, "waiting_phone", context="service")
        await send_telegram_message(chat_id, "قبل المتابعة، أرسل رقم الجوال:\n\n(أرسل /cancel للإلغاء)")
        return
    if not user["cv_file_name"]:
        set_user_state(chat_id, "waiting_cv_for_service", context="service")
        await send_telegram_message(chat_id, "قبل المتابعة، أرسل السيرة الذاتية الآن كملف PDF أو DOCX:\n\n(أرسل /cancel للإلغاء)")
        return

    set_user_state(chat_id, "waiting_email_subject", context="service")
    await send_telegram_message(chat_id, "أرسل عنوان الرسالة / Subject:\n\n(أرسل /cancel للإلغاء)")


async def submit_current_service(chat_id, user):
    training_stats = get_training_stats_for_user(user) if user["service_type"] == "تقديم تدريب تعاوني" else None

    application = {
        "chat_id": chat_id,
        "service_type": user["service_type"],
        "region_label": user["region_label"],
        "selected_cities": user["selected_cities"],
        "qualification": user["qualification"],
        "gender": user["gender"],
        "specialization": user["specialization"],
        "full_name": user["full_name"],
        "phone": user["phone"],
        "cv_file_id": user["cv_file_id"],
        "cv_file_name": user["cv_file_name"],
        "email_subject": user["email_subject"],
        "email_body": user["email_body"],
        "training_total_contacts": training_stats["total"] if training_stats else 0,
        "training_email_contacts": training_stats["email_count"] if training_stats else 0,
        "training_website_contacts": training_stats["website_count"] if training_stats else 0,
        "training_unknown_contacts": training_stats["unknown_count"] if training_stats else 0,
        "submitted_at": datetime.utcnow().isoformat()
    }

    save_application(application)
    await notify_admin_application(application)

    summary = (
        "✅ تم استلام طلبك بنجاح\n\n"
        f"نوع الخدمة: {application['service_type']}\n"
        f"الاسم: {application['full_name']}\n"
        f"المنطقة: {application['region_label']}\n"
        f"المدن: {'، '.join(application['selected_cities'])}\n"
        f"التخصص: {application['specialization']}\n"
        f"المؤهل: {application['qualification']}\n"
        f"الجنس: {application['gender']}\n"
        f"الجوال: {application['phone']}\n"
        f"عنوان الرسالة: {application['email_subject']}\n"
        f"السيرة الذاتية: {application['cv_file_name']}"
    )

    if application["service_type"] == "تقديم تدريب تعاوني":
        summary += (
            f"\n\n📊 جهات التدريب المطابقة:"
            f"\nإجمالي الجهات: {application['training_total_contacts']}"
            f"\nبريد إلكتروني: {application['training_email_contacts']}"
            f"\nعبر الموقع: {application['training_website_contacts']}"
            f"\nغير محدد: {application['training_unknown_contacts']}"
        )

    await send_telegram_message(chat_id, summary, main_menu())


@app.get("/")
async def root():
    return {"status": "ok"}


@app.post("/webhook")
async def telegram_webhook(request: Request, x_telegram_bot_api_secret_token: str = Header(None)):
    if x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        logger.warning("Unauthorized webhook attempt blocked")
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        update = await request.json()
    except Exception:
        logger.exception("Invalid JSON payload received")
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

    user = get_or_create_user(chat_id)

    if data == "noop":
        await answer_callback_query(callback_id, "تم")
        return {"ok": True}

    if data == "back_main":
        set_user_state(chat_id, "")
        await answer_callback_query(callback_id, "القائمة الرئيسية")
        await send_telegram_message(chat_id, "رجعنا للقائمة الرئيسية:", main_menu())
        return {"ok": True}

    if data == "start_profile_setup":
        update_user(chat_id, region_key="", region_label="", selected_cities=[], qualification="", gender="")
        await answer_callback_query(callback_id, "إعداد الملف")
        await send_telegram_message(chat_id, "👋 أهلاً! سنبدأ إعداد ملفك الوظيفي.\n\n📌 الخطوة 1 من 4: اختر منطقتك", region_menu())
        return {"ok": True}

    if data.startswith("region:"):
        region_key = data.split("region:", 1)[1]
        if region_key not in REGION_CITIES:
            await answer_callback_query(callback_id, "منطقة غير صالحة")
            return {"ok": True}

        update_user(chat_id, region_key=region_key, region_label=REGION_CITIES[region_key]["label"], selected_cities=[])
        user = get_or_create_user(chat_id)
        await answer_callback_query(callback_id, "تم اختيار المنطقة")
        await send_telegram_message(
            chat_id,
            f"📍 {REGION_CITIES[region_key]['label']} — اختر مدنك\nيمكنك اختيار أكثر من مدينة ثم اضغط تم ✅",
            cities_multi_menu(user)
        )
        return {"ok": True}

    if data.startswith("toggle_city:"):
        city = data.split("toggle_city:", 1)[1]
        selected = user["selected_cities"]
        if city in selected:
            selected.remove(city)
            await answer_callback_query(callback_id, f"تم إلغاء {city}")
        else:
            selected.append(city)
            await answer_callback_query(callback_id, f"تم اختيار {city}")

        update_user(chat_id, selected_cities=selected)
        user = get_or_create_user(chat_id)
        await send_telegram_message(
            chat_id,
            f"📍 {user['region_label']} — اختر مدنك\nيمكنك اختيار أكثر من مدينة ثم اضغط تم ✅",
            cities_multi_menu(user)
        )
        return {"ok": True}

    if data == "cities_done":
        if not user["selected_cities"]:
            await answer_callback_query(callback_id, "اختر مدينة واحدة على الأقل")
            await send_telegram_message(chat_id, "لازم تختار مدينة واحدة على الأقل.", cities_multi_menu(user))
            return {"ok": True}

        await answer_callback_query(callback_id, "انتقلنا للمؤهل")
        await send_telegram_message(chat_id, "📌 الخطوة 3 من 4: ما هو مؤهلك العلمي؟", qualification_menu())
        return {"ok": True}

    if data == "back_to_cities":
        await answer_callback_query(callback_id, "رجوع للمدن")
        await send_telegram_message(
            chat_id,
            f"📍 {user['region_label']} — اختر مدنك\nيمكنك اختيار أكثر من مدينة ثم اضغط تم ✅",
            cities_multi_menu(user)
        )
        return {"ok": True}

    if data.startswith("qualification:"):
        qualification = data.split("qualification:", 1)[1]
        update_user(chat_id, qualification=qualification)
        await answer_callback_query(callback_id, "تم اختيار المؤهل")
        await send_telegram_message(chat_id, "📌 الخطوة 4 من 4: حدد جنسك", gender_menu())
        return {"ok": True}

    if data == "back_to_qualification":
        await answer_callback_query(callback_id, "رجوع للمؤهل")
        await send_telegram_message(chat_id, "📌 الخطوة 3 من 4: ما هو مؤهلك العلمي؟", qualification_menu())
        return {"ok": True}

    if data.startswith("gender:"):
        gender_key = data.split("gender:", 1)[1]
        gender_label = "ذكر" if gender_key == "male" else "أنثى"
        update_user(chat_id, gender=gender_label)
        user = get_or_create_user(chat_id)
        await answer_callback_query(callback_id, "تم إعداد الملف")
        await send_telegram_message(chat_id, setup_success_html(user), services_after_setup_menu(), parse_mode="HTML")
        return {"ok": True}

    if data == "profile":
        await answer_callback_query(callback_id, "ملفك الوظيفي")
        await send_telegram_message(chat_id, profile_summary(user), main_menu())
        return {"ok": True}

    if data == "preferences":
        await answer_callback_query(callback_id, "تفضيلاتي")
        await send_telegram_message(chat_id, "📌 الإعدادات المتبقية\n\nكلما أكملت بيانات أكثر، صارت الخدمة أدق.", preferences_menu())
        return {"ok": True}

    if data == "pref_specialization":
        set_user_state(chat_id, "waiting_specialization", context="preferences")
        await answer_callback_query(callback_id, "التخصص")
        await send_telegram_message(chat_id, "أرسل تخصصك أو مجالك المستهدف:\n\n(أرسل /cancel للإلغاء)")
        return {"ok": True}

    if data == "pref_full_name":
        set_user_state(chat_id, "waiting_full_name", context="preferences")
        await answer_callback_query(callback_id, "الاسم الكامل")
        await send_telegram_message(chat_id, "أرسل اسمك الكامل:\n\n(أرسل /cancel للإلغاء)")
        return {"ok": True}

    if data == "pref_phone":
        set_user_state(chat_id, "waiting_phone", context="preferences")
        await answer_callback_query(callback_id, "رقم الجوال")
        await send_telegram_message(chat_id, "أرسل رقم الجوال:\n\n(أرسل /cancel للإلغاء)")
        return {"ok": True}

    if data == "pref_cv":
        set_user_state(chat_id, "waiting_cv_only", context="preferences")
        await answer_callback_query(callback_id, "رفع السيرة")
        await send_telegram_message(chat_id, "أرسل السيرة الذاتية الآن كملف PDF أو DOCX:\n\n(أرسل /cancel للإلغاء)")
        return {"ok": True}

    if data == "cv_menu":
        await answer_callback_query(callback_id, "السيرة الذاتية")
        await send_telegram_message(chat_id, "يمكنك رفع أو تحديث سيرتك الذاتية من هنا:", cv_menu())
        return {"ok": True}

    if data == "upload_cv":
        set_user_state(chat_id, "waiting_cv_only", context="preferences")
        await answer_callback_query(callback_id, "رفع السيرة")
        await send_telegram_message(chat_id, "أرسل السيرة الذاتية الآن كملف PDF أو DOCX:\n\n(أرسل /cancel للإلغاء)")
        return {"ok": True}

    if data == "services":
        await answer_callback_query(callback_id, "الخدمات")
        await send_telegram_message(chat_id, "اختر نوع الخدمة المطلوبة:", services_menu())
        return {"ok": True}

    if data == "service_training":
        await answer_callback_query(callback_id, "التدريب التعاوني")
        if not user["selected_cities"]:
            await send_telegram_message(chat_id, "لا يمكن عرض جهات التدريب الآن لأنك لم تحدد مدنك بعد.\nابدأ بإعداد ملفك أولًا.", main_menu())
            return {"ok": True}

        update_user(chat_id, service_type="تقديم تدريب تعاوني")
        await send_telegram_message(chat_id, build_training_preview_html(user, limit=5), training_preview_menu(), parse_mode="HTML")
        return {"ok": True}

    if data == "training_apply":
        await answer_callback_query(callback_id, "ابدأ التقديم")
        await start_service_flow(chat_id, user, "تقديم تدريب تعاوني")
        return {"ok": True}

    if data == "service_companies":
        await answer_callback_query(callback_id, "الشركات السعودية")
        await start_service_flow(chat_id, user, "إرسال للشركات السعودية")
        return {"ok": True}

    if data == "service_cyber":
        await answer_callback_query(callback_id, "جهات السايبر")
        await start_service_flow(chat_id, user, "إرسال لجهات السايبر")
        return {"ok": True}

    if data == "service_cv_design":
        await answer_callback_query(callback_id, "تصميم السيرة")
        await start_service_flow(chat_id, user, "تصميم السيرة الذاتية")
        return {"ok": True}

    if data == "my_requests":
        await answer_callback_query(callback_id, "طلباتك السابقة")
        requests_list = get_user_requests(chat_id)
        if not requests_list:
            await send_telegram_message(chat_id, "لا توجد لديك طلبات سابقة حاليًا.", main_menu())
            return {"ok": True}

        lines = ["📦 طلباتك السابقة:\n"]
        for i, item in enumerate(requests_list[:5], start=1):
            lines.append(f"{i}. {item.get('service_type', 'طلب')} - {item.get('submitted_at', '')[:19]}")
        await send_telegram_message(chat_id, "\n".join(lines), main_menu())
        return {"ok": True}

    if data == "plans":
        await answer_callback_query(callback_id, "الباقات")
        await send_telegram_message(chat_id, "💳 الباقات المتاحة:\n\n🆓 تجريبي\n📅 شهري\n🗓️ نصف سنوي\n📆 سنوي", plans_menu())
        return {"ok": True}

    if data == "plan_trial":
        await answer_callback_query(callback_id, "التجريبي")
        await send_telegram_message(chat_id, "الباقة التجريبية: تجربة محدودة.", main_menu())
        return {"ok": True}

    if data == "plan_monthly":
        await answer_callback_query(callback_id, "الشهري")
        await send_telegram_message(chat_id, "الباقة الشهرية: اشتراك لمدة شهر.", main_menu())
        return {"ok": True}

    if data == "plan_halfyear":
        await answer_callback_query(callback_id, "النصف سنوي")
        await send_telegram_message(chat_id, "الباقة النصف السنوية: اشتراك لمدة 6 أشهر.", main_menu())
        return {"ok": True}

    if data == "plan_yearly":
        await answer_callback_query(callback_id, "السنوي")
        await send_telegram_message(chat_id, "الباقة السنوية: اشتراك لمدة 12 شهرًا.", main_menu())
        return {"ok": True}

    if data == "support":
        await answer_callback_query(callback_id, "الدعم")
        await send_telegram_message(chat_id, "💬 للدعم والاستفسارات:\nالبريد: example@example.com\nالهاتف: 0500000000", main_menu())
        return {"ok": True}

    return {"ok": True}


async def handle_message(message):
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    if not chat_id:
        return {"ok": True}

    text = message.get("text", "").strip()
    user = get_or_create_user(chat_id)

    if text == "/start":
        set_user_state(chat_id, "")
        await send_telegram_message(chat_id, "أهلًا بك في بوت التوظيف 👋\n\nابدأ إعداد ملفك الوظيفي من هنا:", main_menu())
        return {"ok": True}

    if text == "/help":
        await send_telegram_message(chat_id, "استخدم الأزرار للتنقل داخل البوت، وابدأ من: 🚀 ابدأ إعداد الملف", main_menu())
        return {"ok": True}

    if text == "/cancel":
        set_user_state(chat_id, "")
        await send_telegram_message(chat_id, "❌ تم إلغاء العملية الحالية.", main_menu())
        return {"ok": True}

    current_state, context = get_user_state_full(chat_id)

    if "document" in message:
        document = message["document"]
        file_id = document.get("file_id", "")
        file_name = document.get("file_name", "unknown_file")

        allowed_ext = (".pdf", ".docx", ".doc")
        if not file_name.lower().endswith(allowed_ext):
            await send_telegram_message(chat_id, "⚠️ الرجاء رفع ملف بصيغة PDF أو DOCX فقط.")
            return {"ok": True}

        update_user(chat_id, cv_file_id=file_id, cv_file_name=file_name)

        if current_state == "waiting_cv_only":
            set_user_state(chat_id, "")
            await send_telegram_message(chat_id, f"✅ تم تحديث سيرتك الذاتية بنجاح:\n{file_name}", preferences_menu())
            return {"ok": True}

        if current_state == "waiting_cv_for_service":
            set_user_state(chat_id, "waiting_email_subject", context="service")
            await send_telegram_message(chat_id, f"✅ تم استلام السيرة الذاتية:\n{file_name}\n\nالآن أرسل عنوان الرسالة / Subject:")
            return {"ok": True}

        return {"ok": True}

    if current_state == "waiting_specialization":
        if len(text) < 2:
            await send_telegram_message(chat_id, "⚠️ الرجاء إدخال تخصص صحيح (حرفين على الأقل).")
            return {"ok": True}
        update_user(chat_id, specialization=text)
        set_user_state(chat_id, "")
        if context == "service":
            user = get_or_create_user(chat_id)
            await start_service_flow(chat_id, user, user["service_type"])
        else:
            await send_telegram_message(chat_id, "✅ تم حفظ التخصص بنجاح.", preferences_menu())
        return {"ok": True}

    if current_state == "waiting_full_name":
        if len(text) < 3:
            await send_telegram_message(chat_id, "⚠️ الرجاء إدخال اسم صحيح (3 أحرف على الأقل).")
            return {"ok": True}
        update_user(chat_id, full_name=text)
        set_user_state(chat_id, "")
        if context == "service":
            user = get_or_create_user(chat_id)
            await start_service_flow(chat_id, user, user["service_type"])
        else:
            await send_telegram_message(chat_id, "✅ تم حفظ الاسم الكامل.", preferences_menu())
        return {"ok": True}

    if current_state == "waiting_phone":
        digits_only = text.replace(" ", "").replace("-", "")
        if not digits_only.isdigit() or len(digits_only) < 9:
            await send_telegram_message(chat_id, "⚠️ الرجاء إدخال رقم جوال صحيح (أرقام فقط).")
            return {"ok": True}
        update_user(chat_id, phone=text)
        set_user_state(chat_id, "")
        if context == "service":
            user = get_or_create_user(chat_id)
            await start_service_flow(chat_id, user, user["service_type"])
        else:
            await send_telegram_message(chat_id, "✅ تم حفظ رقم الجوال.", preferences_menu())
        return {"ok": True}

    if current_state == "waiting_email_subject":
        if len(text) < 2:
            await send_telegram_message(chat_id, "⚠️ الرجاء إدخال عنوان صحيح.")
            return {"ok": True}
        update_user(chat_id, email_subject=text)
        set_user_state(chat_id, "waiting_email_body", context="service")
        await send_telegram_message(chat_id, "أرسل نص الرسالة التي تريد اعتمادها:\n\n(أرسل /cancel للإلغاء)")
        return {"ok": True}

    if current_state == "waiting_email_body":
        if len(text) < 5:
            await send_telegram_message(chat_id, "⚠️ الرجاء إدخال نص رسالة أطول.")
            return {"ok": True}
        update_user(chat_id, email_body=text)
        set_user_state(chat_id, "")
        user = get_or_create_user(chat_id)

        if not can_submit_service(user):
            await send_telegram_message(
                chat_id,
                "⚠️ لا يمكن إتمام الطلب بعد.\nتأكد من إكمال: التخصص، الاسم، الجوال، والسيرة الذاتية.",
                preferences_menu()
            )
            return {"ok": True}

        await submit_current_service(chat_id, user)
        return {"ok": True}

    await send_telegram_message(chat_id, "اختر من القائمة التالية:", main_menu())
    return {"ok": True}
