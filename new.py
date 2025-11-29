# app.py
import os
import io
import re
import json
import requests
from flask import Flask, request, jsonify
from PIL import Image
from pdf2image import convert_from_bytes
import pytesseract
from groq import Groq

# Tesseract Path (Render uses Linux default path)
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# Groq Client
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

app = Flask(__name__)

# ---------------- HELPERS ----------------
def normalize_number(s):
    if s is None: 
        return None
    s = str(s).replace(",", "").strip()
    try: 
        return float(s)
    except:
        return None

def parse_number(s):
    if s is None:
        return None
    s = str(s)
    m = re.search(r"\d+(\.\d+)?", s)
    return normalize_number(m.group()) if m else None

# ---------------- PDF → IMAGE ----------------
def pages_from_bytes(file_bytes, ext):
    if ext.lower().endswith(".pdf"):
        return convert_from_bytes(file_bytes, dpi=300)
    return [Image.open(io.BytesIO(file_bytes)).convert("RGB")]

# ---------------- OCR ----------------
def ocr_text(img):
    return pytesseract.image_to_string(img, config="--psm 6")

# ---------------- GROQ EXTRACTOR ----------------
def groq_extract_table(text):

    prompt = f"""
Extract ONLY the invoice line items from this OCR text.

Return STRICT JSON only:

{{
  "rows": [
    {{
      "item_name": "<string>",
      "item_quantity": <number or null>,
      "item_rate": <number or null>,
      "item_amount": <number or null>
    }}
  ]
}}

Rules:
- item_name = description column
- item_quantity = Qty column
- item_rate = Rate column
- item_amount = Gross Amount column
- If amount is not present, you may leave it null.
- Ignore TOTAL / Category Total / summary rows.
OCR TEXT:
{text}
"""

    try:
        completion = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_completion_tokens=2048,
            top_p=1,
            reasoning_effort="medium",
            stream=False
        )

        reply = completion.choices[0].message.content

        json_str = re.search(r"\{.*\}", reply, re.S).group(0)
        return json.loads(json_str)

    except Exception as e:
        print("GROQ ERROR:", e)
        return None

# ---------------- MAIN PROCESSOR ----------------
def process_document(file_bytes, ext):
    pages = pages_from_bytes(file_bytes, ext)

    final_pages = []
    all_items = []

    for idx, img in enumerate(pages, start=1):

        text = ocr_text(img)
        structured = groq_extract_table(text)

        rows = structured["rows"] if structured else []

        items = []
        for r in rows:

            qty = parse_number(r.get("item_quantity"))
            rate = parse_number(r.get("item_rate"))
            amt = parse_number(r.get("item_amount"))

            # ⭐ AUTO-CALCULATE missing amount
            if amt is None and qty is not None and rate is not None:
                amt = qty * rate

            items.append({
                "item_name": r.get("item_name"),
                "item_quantity": qty,
                "item_rate": rate,
                "item_amount": amt
            })

        final_pages.append({"page_no": idx, "bill_items": items})
        all_items.extend(items)

    # ⭐ Recalculate total correctly
    total = 0
    for it in all_items:
        qty = it["item_quantity"] or 1
        rate = it["item_rate"] or 0
        amt = it["item_amount"]

        # compute again if still None
        if amt is None:
            amt = qty * rate

        total += amt

    return {
        "is_success": True,
        "data": {
            "pagewise_line_items": final_pages,
            "total_item_count": len(all_items),
            "reconciled_amount": round(total, 2)
        }
    }

# ---------------- ROUTE ----------------
@app.route("/extract-bill-data", methods=["POST"])
def extract_bill_data():
    body = request.get_json()

    if not body or "document" not in body:
        return jsonify({"is_success": False, "error": "Missing 'document'"}), 400

    url = body["document"]

    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
    except:
        return jsonify({"is_success": False, "error": "Download failed"}), 400

    ext = os.path.splitext(url.split("?")[0])[1] or ".png"
    return jsonify(process_document(r.content, ext))

# ---------------- SERVER RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
