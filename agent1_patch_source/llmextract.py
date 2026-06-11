from __future__ import annotations

import json
import re
import typing
from typing import Any, Type, get_args, get_origin

from groq import AsyncGroq
from pydantic import BaseModel, ValidationError

from agent1_patch_source.config import settings


class LLMError(RuntimeError):
    pass


def _client() -> AsyncGroq:
    if not settings.groq_api_key:
        raise LLMError("Missing GROQ_API_KEY in environment.")
    return AsyncGroq(api_key=settings.groq_api_key)


def _build_example_template(model_cls: Type[BaseModel]) -> str:
    def field_placeholder(name: str, annotation) -> Any:
        origin = get_origin(annotation)
        args = get_args(annotation)
        if origin is typing.Union:
            non_none = [a for a in args if a is not type(None)]
            if non_none:
                return field_placeholder(name, non_none[0])
            return None
        if origin is list:
            inner = args[0] if args else str
            if isinstance(inner, type) and issubclass(inner, BaseModel):
                return [build_example(inner, suffix=" (year 1)"), build_example(inner, suffix=" (year 2)")]
            return [f"<{name}_value>"]
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return build_example(annotation)
        if annotation in {float, int}:
            return 0.0
        if annotation is bool:
            return False
        return f"<{name}>"

    def build_example(cls: Type[BaseModel], suffix: str = "") -> dict:
        out = {}
        for fname, finfo in cls.model_fields.items():
            placeholder = field_placeholder(fname, finfo.annotation)
            if isinstance(placeholder, str) and suffix:
                placeholder = placeholder.rstrip(">") + suffix + ">"
            out[fname] = placeholder
        return out

    return json.dumps(build_example(model_cls), ensure_ascii=False, indent=2)


def _schema_instructions(model_cls: Type[BaseModel]) -> str:
    template = _build_example_template(model_cls)
    synonym_hints = (
        "\nFIELD SYNONYM HINTS (Indian financial documents):\n"
        "- short_term_borrowing       : working capital loan, CC limit, cash credit, overdraft, OD, short term loan, STL, WCDL\n"
        "- long_term_borrowing        : term loan, TL, NCD, debenture, long term debt\n"
        "- revenue_from_operations    : net sales, turnover, net turnover, income from operations, gross revenue\n"
        "- cost_of_material           : raw material consumed, COGS, cost of goods sold, purchases of stock-in-trade\n"
        "- employee_benefit_expense   : staff cost, salaries and wages, manpower cost, personnel expenses, payroll\n"
        "- finance_cost               : interest expense, interest on borrowings, bank charges, financial charges\n"
        "- operating_activities       : cash from operations, net cash from operating, cash generated from operations\n"
        "- investing_activities       : cash used in investing, capital expenditure, capex\n"
        "- financing_activities       : cash from financing, proceeds from borrowings, repayment of loans\n"
    )
    return (
        "Return ONLY valid JSON that matches this exact structure.\n"
        "No markdown. No explanation. No extra text.\n"
        "Fill in real extracted values. Do NOT copy this template literally.\n\n"
        f"Expected output structure (fill with real values):\n{template}\n\n"
        "Rules:\n"
        "- Use null for any field where the value is unknown or not present.\n"
        "- Numeric fields must be plain numbers (no currency symbols or commas).\n"
        "- Dates must be YYYY-MM-DD format when present.\n"
        "- Extract ALL financial years present in the documents.\n"
        "- Each entry in the list MUST represent exactly ONE year.\n"
        "- Do NOT merge multiple years into a single entry.\n"
        "- Do NOT skip any year if data exists.\n"
        "- Normalize year formats: FY23 → 2023, 2022-23 → 2023.\n"
        f"{synonym_hints}"
    )


def _try_json(raw: str) -> Any:
    raw = (raw or "").strip()
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    if fence_match:
        candidate = fence_match.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start = raw.find(start_char)
        if start == -1:
            continue
        end = raw.rfind(end_char)
        if end == -1 or end < start:
            continue
        candidate = raw[start:end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise LLMError(f"LLM did not return valid JSON: {e}\nRaw response (first 500 chars):\n{raw[:500]}")


async def _call_llm(client: AsyncGroq, system: str, user: str) -> str:
    response = await client.chat.completions.create(
        model=settings.groq_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0,
        timeout=settings.request_timeout_s,
    )
    content = response.choices[0].message.content
    if not content:
        raise LLMError("Groq returned an empty response.")
    return content.strip()


async def extract_json_with_llm(*, document_text: str, model_cls: Type[BaseModel], doc_hint: str = "financial_document") -> BaseModel:
    text = (document_text or "").strip()
    if not text:
        raise LLMError("No text available for extraction.")
    if len(text) > settings.max_chars_to_llm:
        half = settings.max_chars_to_llm // 2
        text = text[:half] + "\n...[truncated]...\n" + text[-half:]

    system = (
        "You are an expert financial document extraction engine.\n"
        "Extract structured data from the provided document text.\n"
        "You MUST extract every financial year separately — never merge years.\n"
        "Output ONLY valid JSON. No explanation. No markdown."
    )
    rules = _schema_instructions(model_cls)
    prompt = (
        f"Document type: {doc_hint}\n\n"
        "EXTRACTION RULES:\n"
        "- Extract ALL financial years present in the documents.\n"
        "- Return one list entry per year — never merge years.\n"
        "- Use null for missing fields.\n\n"
        f"{rules}\n\n"
        "Document text to extract from:\n"
        "----- START -----\n"
        f"{text}\n"
        "----- END -----\n"
    )

    client = _client()
    raw = await _call_llm(client, system=system, user=prompt)
    data = _try_json(raw)
    if isinstance(data, dict) and "entries" in data and len(data["entries"]) <= 1:
        repair_prompt = (
            f"{rules}\n\n"
            "WARNING: Your previous output did NOT include all years.\n"
            "STRICT REQUIREMENT:\n"
            "- Look through the entire document again.\n"
            "- Extract ALL years present.\n"
            "- Return multiple list entries if multiple years exist.\n"
            "- Output ONLY valid JSON. No explanation.\n\n"
            f"Previous JSON:\n{raw}\n\n"
            "Return corrected JSON with ALL years."
        )
        raw = await _call_llm(client, system=system, user=repair_prompt)
        data = _try_json(raw)

    try:
        return model_cls.model_validate(data)
    except ValidationError as ve:
        repair_prompt = (
            f"{rules}\n\n"
            "Your previous JSON did not validate against the target schema. Fix it.\n"
            f"Validation errors:\n{ve}\n\n"
            f"Previous JSON:\n{json.dumps(data, ensure_ascii=False, indent=2)}\n\n"
            "Return corrected JSON only."
        )
        raw2 = await _call_llm(client, system=system, user=repair_prompt)
        data2 = _try_json(raw2)
        return model_cls.model_validate(data2)
