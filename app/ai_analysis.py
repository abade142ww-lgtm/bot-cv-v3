from openai import OpenAI
from app.config import PERPLEXITY_API_KEY

client = OpenAI(
    api_key=PERPLEXITY_API_KEY,
    base_url="https://api.perplexity.ai"
)

async def analyze_cv(cv_text: str, job_title: str = "") -> str:
    prompt = f"""حلل السيرة الذاتية التالية بدقة واعطِ تقييمًا واقعيًا فقط بناءً على النص المذكور، بدون اختلاق أي معلومة غير موجودة.
الوظيفة المستهدفة: {job_title or 'غير محددة'}

السيرة الذاتية:
{cv_text}

المطلوب:
1. تقييم عام من 100
2. نقاط القوة
3. نقاط تحتاج تحسين
4. توافقها مع نظام ATS
"""
    response = client.responses.create(
        model="openai/gpt-5-mini",
        input=prompt
    )
    return response.output_text
