"""Generate Instagram captions from each video's TikTok metadata.

Runs only on the Mac. ``ANTHROPIC_API_KEY`` lives in ``.env`` and is never a
GitHub secret -- the publish job reads captions that were already frozen into
the queue, so the runner needs no AI credentials at all.

Three properties keep this cheap and safe to re-run:

* **Fingerprint cache** -- a video whose metadata and prompt version are
  unchanged is skipped, so re-running over 200 videos costs nothing.
* **Deterministic validation** -- length, hook and hashtag rules are plain code,
  not another model call, and a failing caption cannot be approved.
* **Human approval** -- ``plan-queue`` only accepts ``approved`` captions, and
  copies the text into the queue so a later edit cannot silently change a post
  that is already scheduled.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MODEL = "claude-opus-5"
PROMPT_VERSION = "v1"

#: Instagram's hard ceiling is 2200 characters, but the first ~125 are all that
#: show before the "more" fold, so that is where the hook has to live.
MAX_CAPTION_CHARS = 2200

#: Piso baixo de proposito. Ele nasceu em 90, quando as legendas eram paragrafos
#: explicativos, e passou a reprovar justamente as boas quando a voz mudou para
#: algo mais seco: "Ahhh que coisa boa. Eu tinha planos pro resto do dia. Tinha."
#: tem 60 caracteres e diz mais que qualquer paragrafo. O que o piso precisa
#: barrar e a legenda vazia de conteudo -- so a frase da musica sem nada em
#: volta -- e para isso 40 basta.
TARGET_MIN_CHARS = 40
TARGET_MAX_CHARS = 400
HOOK_CHARS = 125

#: Words that leave the hook hanging mid-thought when the caption is cut at the
#: "more" fold. Matched whole, never as a suffix.
DANGLING_WORDS = frozenset(
    {
        "e",
        "ou",
        "mas",
        "que",
        "de",
        "da",
        "do",
        "das",
        "dos",
        "em",
        "no",
        "na",
        "nos",
        "nas",
        "com",
        "sem",
        "por",
        "para",
        "pra",
        "pro",
        "a",
        "o",
        "as",
        "os",
        "um",
        "uma",
        "ao",
        "aos",
        "se",
        "ja",
        "the",
        "and",
        "of",
    }
)
MAX_HASHTAGS = 30
TARGET_HASHTAG_RANGE = (3, 8)

#: Naming the source platform on a repost invites both a policy problem and a
#: reach penalty; "link na bio" is dead weight on an account with no link.
FORBIDDEN_TERMS = ("tiktok", "link na bio", "link in bio")

SYSTEM_PROMPT = """\
Voce escreve legendas de Reels para o perfil brasileiro @_lukasmax, que republica \
os proprios videos virais de humor e nostalgia musical.

Escreva em portugues do Brasil, na primeira pessoa, com a mesma energia informal \
do video. A legenda acompanha o video: ela provoca curiosidade ou emocao, nunca \
descreve o que a pessoa ja esta vendo.

Regras:
- Os primeiros 125 caracteres sao o gancho: e o unico trecho visivel antes do "mais". \
  Comece por ele, nunca por saudacao ou contexto.
- Entre 90 e 400 caracteres no total.
- Termine com um convite leve a interacao (salvar, marcar alguem, comentar), sem soar \
  como pedido de engajamento generico.
- De 3 a 8 hashtags, especificas do tema e da musica, sem hashtags genericas de alcance \
  (#viral, #fyp, #foryou, #explorar).
- Nunca cite TikTok nem outra plataforma, e nunca escreva "link na bio".
- Sem emoji em excesso: no maximo dois, e so se somarem alguma coisa.

O alt_text descreve a cena para quem usa leitor de tela, em uma frase objetiva.\
"""

CAPTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "caption": {
            "type": "string",
            "description": "Legenda completa em pt-BR, sem as hashtags.",
        },
        "hashtags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "De 3 a 8 hashtags, cada uma comecando com #.",
        },
        "alt_text": {
            "type": "string",
            "description": "Descricao da cena em uma frase, para acessibilidade.",
        },
    },
    "required": ["caption", "hashtags", "alt_text"],
    "additionalProperties": False,
}

#: Metadata fields fed to the model. Kept explicit so the fingerprint only
#: changes when something that actually shapes the caption changes.
METADATA_FIELDS = (
    "title",
    "description",
    "track",
    "artists",
    "duration",
    "view_count",
    "like_count",
    "comment_count",
    "repost_count",
    "upload_date",
)


class CaptionError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def build_metadata(
    info_path: Path, *, rank: int | None = None, score: float | None = None
) -> dict[str, Any]:
    """Extract the caption inputs from yt-dlp's sidecar file."""
    if not info_path.exists():
        raise CaptionError(f"Metadados ausentes: {info_path}")
    info = json.loads(info_path.read_text(encoding="utf-8"))
    metadata = {field: info.get(field) for field in METADATA_FIELDS}
    metadata["id"] = info.get("id")
    if rank is not None:
        metadata["rank"] = rank
    if score is not None:
        metadata["score"] = round(float(score), 4)
    return metadata


def fingerprint(metadata: dict[str, Any]) -> str:
    """Stable hash of the inputs, so an unchanged video is never re-billed."""
    payload = json.dumps(
        {"prompt_version": PROMPT_VERSION, "model": MODEL, "metadata": metadata},
        ensure_ascii=False,
        sort_keys=True,
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Validation -- deterministic code, never a model call
# ---------------------------------------------------------------------------


def validate(caption: str, hashtags: list[str]) -> list[str]:
    """Return human-readable warnings. A non-empty list blocks approval."""
    warnings: list[str] = []
    text = (caption or "").strip()
    full_length = len(text) + sum(len(tag) + 1 for tag in hashtags)

    if not text:
        warnings.append("legenda vazia")
    if full_length > MAX_CAPTION_CHARS:
        warnings.append(
            f"legenda com {full_length} caracteres (limite do Instagram: {MAX_CAPTION_CHARS})"
        )
    if text and len(text) < TARGET_MIN_CHARS:
        warnings.append(
            f"legenda muito curta ({len(text)} caracteres, alvo minimo {TARGET_MIN_CHARS})"
        )
    if len(text) > TARGET_MAX_CHARS:
        warnings.append(f"legenda longa ({len(text)} caracteres, alvo maximo {TARGET_MAX_CHARS})")
    if text and text != caption:
        warnings.append("legenda tem espaco em branco nas pontas")

    hook = text[:HOOK_CHARS].rstrip()
    if text and len(text) > HOOK_CHARS:
        # Compare the last *word*, not a suffix: endswith("e") matches "esquece",
        # "importante" and "onde", flagging perfectly good hooks as broken.
        last_word = hook.rstrip(",;:").rsplit(" ", 1)[-1].lower()
        if hook.endswith((",", ";", ":")) or last_word in DANGLING_WORDS:
            warnings.append("o gancho corta no meio de uma frase antes do 'mais'")

    if len(hashtags) > MAX_HASHTAGS:
        warnings.append(f"{len(hashtags)} hashtags (limite do Instagram: {MAX_HASHTAGS})")
    low, high = TARGET_HASHTAG_RANGE
    if not low <= len(hashtags) <= high:
        warnings.append(f"{len(hashtags)} hashtags (alvo: {low} a {high})")
    for tag in hashtags:
        if not tag.startswith("#"):
            warnings.append(f"hashtag sem '#': {tag!r}")
        if " " in tag.strip():
            warnings.append(f"hashtag com espaco: {tag!r}")

    haystack = f"{text} {' '.join(hashtags)}".lower()
    for term in FORBIDDEN_TERMS:
        if term in haystack:
            warnings.append(f"contem termo proibido: {term!r}")

    return warnings


def full_caption(record: dict[str, Any]) -> str:
    """Caption plus hashtags, exactly as it will be posted."""
    text = (record.get("caption") or "").strip()
    tags = " ".join(record.get("hashtags") or [])
    return f"{text}\n\n{tags}".strip() if tags else text


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save(record: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def approve(record: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    """Move a draft to approved, refusing to wave through validation warnings."""
    warnings = validate(record.get("caption") or "", record.get("hashtags") or [])
    record["warnings"] = warnings
    if warnings and not force:
        raise CaptionError(
            f"{record.get('tiktok_id')}: {len(warnings)} aviso(s) impedem a aprovacao "
            f"({'; '.join(warnings)}). Edite o arquivo ou use --force."
        )
    record["status"] = "approved"
    record["approved_at"] = _now()
    return record


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def from_text(
    tiktok_id: str,
    metadata: dict[str, Any],
    *,
    caption: str,
    hashtags: list[str],
    alt_text: str = "",
    author: str = "human",
) -> dict[str, Any]:
    """Build a caption record from text written by hand.

    Same shape and the same validation as a generated one, so an imported
    caption is indistinguishable downstream -- and carries a real fingerprint,
    so a later ``draft-captions`` run treats it as current instead of
    overwriting it.
    """
    caption = caption.strip()
    hashtags = [str(tag).strip() for tag in hashtags]
    return {
        "tiktok_id": tiktok_id,
        "prompt_version": PROMPT_VERSION,
        "model": author,
        "generated_at": _now(),
        "input_fingerprint": fingerprint(metadata),
        "caption": caption,
        "hashtags": hashtags,
        "alt_text": alt_text.strip(),
        "warnings": validate(caption, hashtags),
        "status": "draft",
        "edited_by_human": True,
        "approved_at": None,
        "history": [],
    }


def _client():
    try:
        import anthropic
    except ImportError as error:
        raise CaptionError(
            "O SDK da Anthropic nao esta instalado. Rode: uv sync --extra local"
        ) from error
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise CaptionError("ANTHROPIC_API_KEY nao esta definida no .env")
    return anthropic.Anthropic()


def generate(metadata: dict[str, Any], *, client: Any | None = None) -> dict[str, Any]:
    """Ask Claude for one caption, with the shape guaranteed by the schema."""
    client = client or _client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            }
        ],
        output_config={"format": {"type": "json_schema", "schema": CAPTION_SCHEMA}},
    )
    if getattr(response, "stop_reason", None) == "refusal":
        raise CaptionError(f"O modelo recusou o pedido para {metadata.get('id')}")
    text = next((block.text for block in response.content if block.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise CaptionError(f"Resposta nao era JSON valido: {text[:200]}") from error


def draft(
    tiktok_id: str,
    metadata: dict[str, Any],
    path: Path,
    *,
    client: Any | None = None,
    force: bool = False,
) -> tuple[dict[str, Any], bool]:
    """Generate or reuse a caption. Returns the record and whether it was fresh."""
    stamp = fingerprint(metadata)
    existing = load(path)
    if existing and existing.get("input_fingerprint") == stamp and not force:
        return existing, False

    result = generate(metadata, client=client)
    caption = (result.get("caption") or "").strip()
    hashtags = [str(tag).strip() for tag in (result.get("hashtags") or [])]

    record: dict[str, Any] = {
        "tiktok_id": tiktok_id,
        "prompt_version": PROMPT_VERSION,
        "model": MODEL,
        "generated_at": _now(),
        "input_fingerprint": stamp,
        "caption": caption,
        "hashtags": hashtags,
        "alt_text": (result.get("alt_text") or "").strip(),
        "warnings": validate(caption, hashtags),
        "status": "draft",
        "edited_by_human": False,
        "approved_at": None,
        "history": (existing or {}).get("history", []),
    }
    if existing and existing.get("caption"):
        record["history"] = [
            *record["history"],
            {
                "at": existing.get("generated_at"),
                "model": existing.get("model"),
                "caption": existing.get("caption"),
            },
        ][-5:]
    return record, True
