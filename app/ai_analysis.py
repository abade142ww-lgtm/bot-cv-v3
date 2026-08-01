import io
import pdfplumber
from docx import Document
from openai import OpenAI
from app.config import PERPLEXITY_API_KEY

client = OpenAI(
    api_key=PERPLEXITY_API_KEY,
    base_url="https://api.perplexity.ai"
)

def extract_text_from_pdf(file_bytes: bytes) -> str:
    text = ""
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
    return text.strip()

def extract_text_from_docx(file_bytes: bytes) -> str:
    doc = Document(io.BytesIO(file_bytes))
    return "\n".join(p.text for p in doc.paragraphs).strip()

def extract_cv_text(file_bytes: bytes, filename: str) -> str:
    if filename.lower().endswith(".pdf"):
        return extract_text_from_pdf(file_bytes)
    elif filename.lower().endswith((".docx", ".doc")):
        return extract_text_from_docx(file_bytes)
    return ""

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
