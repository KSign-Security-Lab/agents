"""Post-compose verification: give uncited claims a source or hedge them.

Runs after compose has already streamed its full answer. ``uncited_sentences``
needs finished sentence boundaries, so there is no way to overlap this with the
stream — it is a correction pass, not a gate. It reuses the *same*
``StreamingCitationParser`` instance compose used so a fix that cites an
already-offered source keeps that source's existing pill number, and a newly
cited source simply continues the numbering compose left off.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from api.app.agent import citations as cit
from api.app.agent.prompts import ko
from api.app.services import llm_client

log = logging.getLogger("agent.verify")


@dataclass(slots=True)
class VerifyResult:
    text: str
    citations: list[cit.Citation]
    new_citations: list[cit.Citation]
    fixed_sentences: int
    changed: bool = field(init=False)

    def __post_init__(self) -> None:
        self.changed = self.fixed_sentences > 0


def apply_fixes(
    answer: str,
    fixes: list[dict],
    known_sids: set[int],
    parser: cit.StreamingCitationParser,
) -> tuple[str, list[cit.Citation], int]:
    """Splice validated fixes into ``answer`` using the live ``parser``.

    Pure and safe against a model that doesn't echo the sentence verbatim, or
    that names a ``source_id`` it was never offered: such fixes are skipped
    rather than applied partially.
    """
    text = answer
    new_citations: list[cit.Citation] = []
    fixed = 0

    for fix in fixes:
        sentence = (fix.get("sentence") or "").strip()
        action = fix.get("action", "keep")
        if not sentence or action == "keep" or sentence not in text:
            continue

        replacement = (fix.get("replacement") or "").strip()
        if not replacement:
            continue

        if action == "cite":
            sids = [i for i in fix.get("source_ids", []) if isinstance(i, int) and i in known_sids]
            if not sids:
                continue
            marker_text = replacement + "".join(f"[S{sid}]" for sid in sids)
        elif action == "hedge":
            marker_text = replacement
        else:
            continue

        body, fed = parser.feed(marker_text)
        tail, more = parser.finish()
        text = text.replace(sentence, body + tail, 1)
        new_citations.extend(fed)
        new_citations.extend(more)
        fixed += 1

    return text, new_citations, fixed


async def verify_node(
    answer: str,
    sources: list[cit.SourceRef],
    parser: cit.StreamingCitationParser,
) -> VerifyResult:
    """Check ``answer`` for uncited claims and correct them in place.

    Returns unchanged (zero added latency) when there's nothing to fix, which
    is the common case since the compose prompt already drills citation
    discipline.
    """
    flagged = cit.uncited_sentences(answer)
    if not flagged:
        return VerifyResult(answer, list(parser.citations), [], 0)

    context = cit.build_context_block(sources)
    msgs = [
        {"role": "system", "content": ko.VERIFY_SYSTEM},
        {
            "role": "user",
            "content": ko.VERIFY_USER.format(
                context=context, sentences="\n".join(f"- {s}" for s in flagged)
            ),
        },
    ]
    try:
        raw = await llm_client.complete_json(msgs, ko.VERIFY_SCHEMA, max_tokens=1024)
    except Exception as exc:  # noqa: BLE001
        log.warning("verify pass failed (%s); leaving answer unchanged", exc)
        return VerifyResult(answer, list(parser.citations), [], 0)

    known_sids = {s.sid for s in sources}
    text, new_citations, fixed = apply_fixes(answer, raw.get("fixes", []), known_sids, parser)
    return VerifyResult(text, list(parser.citations), new_citations, fixed)
