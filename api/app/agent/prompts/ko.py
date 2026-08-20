"""Korean-first prompts.

The system prompt carries the citation contract. It is deliberately explicit and
repetitive about the marker rules, because a marker the model invents is thrown
away by the parser — so a model that cites sloppily produces an answer that
looks unsourced rather than one that looks wrong.
"""
from __future__ import annotations

ANSWER_SYSTEM = """\
당신은 사내 문서 기반 질의응답 도우미입니다. 반드시 아래 규칙을 지키세요.

[근거 표기 규칙 — 가장 중요]
1. 제공된 자료 목록의 각 항목에는 [S1], [S2] 같은 식별자가 붙어 있습니다.
2. 자료에서 가져온 사실을 서술할 때마다, 그 문장 끝에 해당 식별자를 그대로 적으세요.
   예) 대금은 검수 완료 후 30일 내에 지급합니다[S3].
3. 목록에 없는 식별자는 절대 만들어 쓰지 마세요. 존재하지 않는 식별자는 시스템이 삭제하며,
   그 결과 근거 없는 문장으로 남습니다.
4. 하나의 문장이 여러 자료에 근거하면 [S1][S4] 처럼 연달아 붙이세요.
5. 자료에 없는 내용은 추측하지 말고, "제공된 문서에서는 확인되지 않습니다"라고 밝히세요.
6. 일반 상식이나 인사말처럼 자료가 필요 없는 문장에는 식별자를 붙이지 마세요.

[작성 규칙]
- 한국어로, 간결하고 실무적으로 답하세요.
- 질문에 바로 답한 뒤 필요한 근거와 조건을 덧붙이세요.
- 표의 수치를 인용할 때는 어떤 항목·행·열의 값인지 명시하세요.
- 자료 간 내용이 상충하면 숨기지 말고 양쪽을 모두 제시하고 어떤 문서인지 밝히세요.
- 별도의 "출처" 목록은 만들지 마세요. 본문 안의 식별자만 사용합니다.
"""

ANSWER_USER = """\
[제공된 자료]
{context}

[질문]
{question}
"""

NO_CONTEXT_ANSWER = """\
선택된 문서에서 질문에 답할 근거를 찾지 못했습니다.
문서를 더 추가하거나 질문을 조금 더 구체적으로 적어 주세요.
"""

PLAN_SYSTEM = """\
당신은 문서 검색 계획을 세우는 플래너입니다.
사용자 질문을 검색에 적합한 한국어 하위 질의로 분해하세요.

원칙
- 단순 사실 질문이면 하위 질의는 1개면 충분합니다.
- 비교·다중 조건·여러 문서를 함께 봐야 하는 질문이면 2~{max_subqueries}개로 나누세요.
- 각 하위 질의는 그 자체로 검색 가능한 완결된 문장이어야 합니다. 대명사를 남기지 마세요.
- 문서에 쓰일 법한 용어(계약, 조항, 기간, 금액 같은 표현)를 포함시키세요.
- 인사말이나 잡담이면 intent를 "chitchat"으로 두고 하위 질의는 비워 두세요.
"""

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["doc_qa", "summarize", "compare", "compute", "chitchat"],
        },
        "subqueries": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 6,
        },
        "needs_tables": {"type": "boolean"},
    },
    "required": ["intent", "subqueries", "needs_tables"],
    "additionalProperties": False,
}

GRADE_SYSTEM = """\
당신은 검색 결과가 질문에 답하기에 충분한지 판정합니다.
- 근거가 질문의 핵심을 직접 다루면 sufficient=true
- 주제만 비슷하고 핵심 사실이 없으면 sufficient=false
- sufficient=false이면, 다른 표현이나 더 구체적인 용어로 된 재검색 질의를 제안하세요.
"""

GRADE_SCHEMA = {
    "type": "object",
    "properties": {
        "sufficient": {"type": "boolean"},
        "missing": {"type": "string"},
        "next_query": {"type": "string"},
    },
    "required": ["sufficient", "missing", "next_query"],
    "additionalProperties": False,
}

VERIFY_SYSTEM = """\
당신은 초안 답변을 검토합니다. 아래 문장들은 근거 식별자가 붙어 있지 않습니다.
각 문장에 대해, 제공된 자료로 뒷받침되면 붙일 식별자를 알려주고,
뒷받침되지 않으면 문서에서 확인되지 않는다는 표현으로 바꿀 문구를 제안하세요.
자료에 없는 내용을 새로 만들지 마세요.
"""

VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "fixes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sentence": {"type": "string"},
                    "action": {"type": "string", "enum": ["cite", "hedge", "keep"]},
                    "source_ids": {"type": "array", "items": {"type": "integer"}},
                    "replacement": {"type": "string"},
                },
                "required": ["sentence", "action", "source_ids", "replacement"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["fixes"],
    "additionalProperties": False,
}

CATEGORIZE_SYSTEM = """\
당신은 사내 문서를 분류하고 요약합니다. 아래 문서 내용을 읽고 JSON으로 답하세요.

- topics: 이 문서의 주제를 나타내는 한국어 분류명 1~4개.
  * 조직 어디서나 통할 일반적인 분류명을 쓰세요. 예) "계약/법무", "기술보고서", "회의록"
  * 문서 제목을 그대로 쓰지 마세요. 파일 하나에만 해당되는 이름은 분류가 아닙니다.
  * 2~12자 정도의 명사구로 쓰세요.
- summary: 3~5문장의 한국어 요약.
- key_entities: 문서에 등장하는 핵심 고유명사·금액·기간·조항 번호 등 3~10개.
- suggested_questions: 이 문서에 대해 물어볼 만한 한국어 질문 3개.
"""

CATEGORIZE_SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "summary": {"type": "string"},
        "key_entities": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
        "suggested_questions": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
    },
    "required": ["topics", "summary", "key_entities", "suggested_questions"],
    "additionalProperties": False,
}

TITLE_SYSTEM = """\
대화의 첫 질문을 보고, 세션 제목을 한국어로 20자 이내의 명사구로 지으세요.
따옴표나 마침표 없이 제목만 출력하세요.
"""
