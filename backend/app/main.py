from __future__ import annotations

import copy
import json
import os
import pathlib
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
import httpx
import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from jsonschema import Draft202012Validator
from openai import APIStatusError, OpenAI, OpenAIError
from pydantic import BaseModel, Field
from sentence_transformers import CrossEncoder

# ======================================================================================
# App setup
# ======================================================================================
app = FastAPI(title="I-ADOPT Variable Decomposition API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # for local development; tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ======================================================================================
# Paths & configuration
# ======================================================================================

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
ROOT_DIR = pathlib.Path(__file__).resolve().parent.parent.parent

load_dotenv(ROOT_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
SCHEMA_PATH = DATA_DIR / "Json_schema.json"
PROMPT_DIR = DATA_DIR / "prompts"
FIVE_SHOT_DIR = DATA_DIR / "Json_preferred" / "five_shot"

MODEL_NAME = os.getenv("MODEL_NAME", "qwen/qwen3.5-397b-a17b")
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.5"))
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

CROSS_ENCODER_ID = os.getenv("CROSS_ENCODER_ID", "tomaarsen/Qwen3-Reranker-0.6B-seq-cls")
RERANK_DEVICE = os.getenv("RERANK_DEVICE", "cpu")
RERANK_THRESHOLD = float(os.getenv("RERANK_THRESHOLD", "0.8"))
ENABLE_WIKIDATA_LINKING = os.getenv("ENABLE_WIKIDATA_LINKING", "true").lower() == "true"

_JSON_FENCE_RE = re.compile(r"```(?:json)?", re.MULTILINE)
_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)

ONTO_KEYS = [
    "hasStatisticalModifier",
    "hasProperty",
    "hasObjectOfInterest",
    "hasMatrix",
    "hasContextObject",
    "hasConstraint",
]


# ======================================================================================
# Request/response models
# ======================================================================================


class DecomposeRequest(BaseModel):
    definition: str = Field(..., min_length=1, description="Variable definition in plain text")


class DecomposeResponse(BaseModel):
    raw_llm_output: str
    parsed_json: Dict[str, Any]
    schema_valid: bool
    validation_errors: List[str]
    enriched_json: Dict[str, Any]
    ttl: str


# ======================================================================================
# Lazy-loaded clients/models
# ======================================================================================

_openai_client: Optional[OpenAI] = None
_reranker: Optional[CrossEncoder] = None


def get_openai_client() -> OpenAI:
    global _openai_client

    if _openai_client is None:
        if not OPENROUTER_API_KEY:
            raise RuntimeError("OPENROUTER_API_KEY is not set.")
        _openai_client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=OPENROUTER_API_KEY,
        )
    return _openai_client


def get_reranker() -> CrossEncoder:
    global _reranker

    if _reranker is None:
        _reranker = CrossEncoder(CROSS_ENCODER_ID, device=RERANK_DEVICE)
    return _reranker


# ======================================================================================
# Prompt building
# ======================================================================================

_EXAMPLE_HDR = "\n\n### Examples (valid against the same schema)\n"
_USER_HDR = "\n\n### Variable's definition to decompose\n"
_EXPECTED_HDR = "\n\n### Expected output\n*(only the JSON object)*"


def list_prompt_versions(prompt_dir: pathlib.Path) -> List[str]:
    if not prompt_dir.exists():
        return []
    return sorted(p.stem for p in prompt_dir.glob("*.txt"))


def load_prompt_instructions(prompt_dir: pathlib.Path, prompt_version: str) -> str:
    versions = list_prompt_versions(prompt_dir)
    if not versions:
        raise RuntimeError(f"No prompt templates found in {prompt_dir}")

    if not prompt_version or prompt_version not in versions:
        prompt_version = versions[0]

    return (prompt_dir / f"{prompt_version}.txt").read_text(encoding="utf-8").strip()


def strip_all_uri_fields(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if "URI" in k:
                continue
            if k.startswith("__"):
                continue
            out[k] = strip_all_uri_fields(v)
        return out
    if isinstance(obj, list):
        return [strip_all_uri_fields(x) for x in obj]
    return obj


def format_example_block(ex: Dict[str, Any], idx: int) -> str:
    definition = ex.get("definition") or ex.get("comment") or ""
    ex_no_uris = strip_all_uri_fields(ex)
    return (
        f"\n\n#### Example {idx}\n"
        f"Variable's definition to decompose: {definition}\n\n"
        f"Expected output:\n{json.dumps(ex_no_uris, indent=2, ensure_ascii=False)}"
    )


def load_examples(folder: pathlib.Path, n: int) -> List[Dict[str, Any]]:
    if n <= 0 or not folder.exists():
        return []
    paths = sorted(folder.glob("*.json"))
    return [json.loads(p.read_text(encoding="utf-8")) for p in paths[:n]]


def build_prompt(definition: str, prompt_version: str, examples: Optional[List[Dict[str, Any]]] = None) -> str:
    examples = examples or []
    instructions = load_prompt_instructions(PROMPT_DIR, prompt_version)
    schema_text = SCHEMA_PATH.read_text(encoding="utf-8").strip() if SCHEMA_PATH.exists() else "{SCHEMA_PLACEHOLDER}"

    ex_block = ""
    if examples:
        blocks = [format_example_block(ex, i + 1) for i, ex in enumerate(examples)]
        ex_block = _EXAMPLE_HDR + "".join(blocks)

    return (
        f"{instructions}\n\n"
        f"### JSON-Schema\n{schema_text}\n"
        f"{ex_block}"
        f"{_USER_HDR}{definition}"
        f"{_EXPECTED_HDR}"
    )


# ======================================================================================
# LLM call + JSON extraction
# ======================================================================================


def call_model(model: str, prompt: str, temperature: float) -> str:
    client = get_openai_client()

    for attempt in range(1, 4):
        try:
            resp = client.chat.completions.create(
                model=model,
                temperature=temperature,
                messages=[{"role": "user", "content": prompt}],
                timeout=60,
            )
            text = resp.choices[0].message.content or ""
            stripped = text.strip()

            if stripped.startswith("<!DOCTYPE html") or stripped.startswith("<html"):
                continue
            if not stripped:
                continue

            return text

        except APIStatusError as e:
            print(f"APIStatusError attempt {attempt}: {e}")
        except (OpenAIError, httpx.HTTPError) as e:
            print(f"Transport error attempt {attempt}: {e}")
        except Exception as e:
            print(f"Unexpected error attempt {attempt}: {e}")

    return ""


def coerce_prediction(pred: Dict[str, Any]) -> Dict[str, Any]:
    pred = dict(pred or {})

    for k in ONTO_KEYS:
        if k not in pred or pred[k] is None:
            pred[k] = [] if k == "hasConstraint" else ""
        elif k == "hasConstraint" and not isinstance(pred[k], list):
            pred[k] = []

    if isinstance(pred.get("hasProperty"), dict):
        pred["hasProperty"] = pred["hasProperty"].get("label", "") or ""

    return pred


def parse_llm_json(raw: str, definition: str) -> Dict[str, Any]:
    cleaned = _JSON_FENCE_RE.sub("", raw).strip()
    match = _JSON_BLOCK_RE.search(cleaned)

    if not match:
        raise ValueError("No JSON object found in model output.")

    try:
        data = json.loads(match.group(0))
    except Exception as e:
        raise ValueError(f"JSON decode failure: {e}") from e

    data["definition"] = definition
    return coerce_prediction(data)


def call_llm_loose(model: str, prompt: str, definition: str, temperature: float) -> Tuple[str, Dict[str, Any]]:
    last_raw = ""

    for attempt in range(1, 4):
        raw = call_model(model, prompt, temperature)
        last_raw = raw

        if not raw.strip():
            continue

        try:
            data = parse_llm_json(raw, definition)
            return raw, data
        except Exception as e:
            print(f"LLM parse attempt {attempt} failed: {e}")

    return last_raw, {}


# ======================================================================================
# JSON Schema validation
# ======================================================================================


def _format_path(err) -> str:
    if not err.path:
        return "$"
    out = "$"
    for p in err.path:
        if isinstance(p, int):
            out += f"[{p}]"
        else:
            out += f".{p}"
    return out


def _safe_preview(value: Any, limit: int = 200) -> str:
    try:
        s = json.dumps(value, ensure_ascii=False)
    except Exception:
        s = repr(value)
    if len(s) > limit:
        s = s[:limit] + "…"
    return s


def _patch_schema_for_pipeline(schema: Dict[str, Any]) -> Dict[str, Any]:
    patched = copy.deepcopy(schema)

    try:
        hc = patched["properties"]["hasConstraint"]
        if isinstance(hc, dict) and hc.get("minItems", None) == 1:
            hc["minItems"] = 0
    except Exception:
        pass

    return patched


def load_schema(schema_path: pathlib.Path) -> Dict[str, Any]:
    if not schema_path.exists():
        raise RuntimeError(f"Schema file not found: {schema_path}")
    return json.loads(schema_path.read_text(encoding="utf-8"))


def get_schema_validation_errors(
    instance: Dict[str, Any],
    *,
    schema_path: pathlib.Path = SCHEMA_PATH,
    schema: Optional[Dict[str, Any]] = None,
    label_for_logs: Optional[str] = None,
) -> List[str]:
    if schema is None:
        schema = load_schema(schema_path)

    schema = _patch_schema_for_pipeline(schema)

    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))

    if not errors:
        return []

    header = "Schema validation failed"
    if label_for_logs:
        header += f" for variable: {label_for_logs}"

    lines: List[str] = [header, "-" * len(header)]

    max_errs = 30
    for i, err in enumerate(errors[:max_errs], start=1):
        path = _format_path(err)
        offending_value = _safe_preview(err.instance)

        lines.append(f"{i:02d}) Path: {path}")
        lines.append(f"    Error: {err.message}")
        lines.append(f"    Offending value: {offending_value}")

        if (
            path.startswith("$.hasObjectOfInterest")
            or path.startswith("$.hasMatrix")
            or path.startswith("$.hasContextObject")
        ):
            lines.append(
                "    Hint: This error is inside an entityOrSystem field "
                "(string vs AsymmetricSystem vs SymmetricSystem)."
            )

    if len(errors) > max_errs:
        lines.append(f"... plus {len(errors) - max_errs} more errors.")

    return lines


# ======================================================================================
# Wikidata linking
# ======================================================================================

QWEN3_RERANK_PREFIX = (
    "<|im_start|>system\n"
    " Judge whether the Document meets the requirements based on the Query and the Instruct provided. "
    'Note that the answer can only be "yes" or "no".<|im_end|>\n'
    "<|im_start|>user\n"
)
QWEN3_RERANK_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
DEFAULT_RERANK_TASK = "Given a web search query, retrieve relevant passages that answer the query"


def format_queries(query: str, task: str = DEFAULT_RERANK_TASK) -> str:
    return f"{QWEN3_RERANK_PREFIX}<Instruct>: {task}\n<Query>: {query}\n"


def format_document(doc: str) -> str:
    return f"<Document>: {doc}{QWEN3_RERANK_SUFFIX}"


def _qid_from_uri_or_text(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    m = re.search(r"(Q\d+)", s)
    return m.group(1) if m else None


def _to_wiki_url(uri: Optional[str]) -> Optional[str]:
    if not uri:
        return None
    q = _qid_from_uri_or_text(uri)
    return f"https://www.wikidata.org/wiki/{q}" if q else uri.strip().replace("http://", "https://")


def get_wikidata_entity_cross_encoder(
    term: str,
    context: str = "",
    threshold: float = RERANK_THRESHOLD,
) -> Optional[str]:
    if not term:
        return None

    encoded = urllib.parse.quote_plus(term)
    headers = {"User-Agent": "IADOPT-Linker/1.0 (+fastapi)"}
    url = "https://www.wikidata.org/w/api.php" f"?action=wbsearchentities&search={encoded}&language=en&format=json"

    response = requests.get(url, headers=headers, timeout=20)
    if response.status_code != 200:
        return None

    search = response.json().get("search", [])
    if not search:
        return None

    query = f'Definition of "{term}" in context: "{context}"'
    documents = [f'label: "{s.get("label", "")}", description: "{s.get("description", "")}"' for s in search]

    pairs = [[format_queries(query, DEFAULT_RERANK_TASK), format_document(doc)] for doc in documents]
    scores = get_reranker().predict(pairs, show_progress_bar=False)

    ranked = sorted(zip(search, scores), key=lambda x: float(x[1]), reverse=True)
    best_s, best_score = ranked[0]

    return _to_wiki_url(best_s["id"]) if float(best_score) >= float(threshold) else None


def enrich_with_uris_cross_encoder(pred: Dict[str, Any], threshold: float = RERANK_THRESHOLD) -> Dict[str, Any]:
    out = json.loads(json.dumps(pred))

    def add_uri_field(container: Dict[str, Any], key: str, label_value: Any):
        if isinstance(label_value, str) and label_value.strip():
            uri = get_wikidata_entity_cross_encoder(
                label_value,
                context=pred.get("definition", ""),
                threshold=threshold,
            )
            if uri:
                container[f"{key}URI"] = _to_wiki_url(uri)

    for p in ["hasProperty", "hasMatrix", "hasObjectOfInterest", "hasContextObject", "hasStatisticalModifier"]:
        if p in out and isinstance(out[p], str):
            add_uri_field(out, p, out[p])

    for p in ["hasMatrix", "hasObjectOfInterest", "hasContextObject"]:
        val = out.get(p)
        if isinstance(val, dict):
            if "AsymmetricSystem" in val:
                for kk in ["AsymmetricSystem", "hasSource", "hasTarget"]:
                    if val.get(kk):
                        uri = get_wikidata_entity_cross_encoder(
                            val[kk],
                            context=pred.get("definition", ""),
                            threshold=threshold,
                        )
                        if uri:
                            val[f"{kk}URI"] = _to_wiki_url(uri)

            if "SymmetricSystem" in val:
                if val.get("SymmetricSystem"):
                    uri = get_wikidata_entity_cross_encoder(
                        val["SymmetricSystem"],
                        context=pred.get("definition", ""),
                        threshold=threshold,
                    )
                    if uri:
                        val["SymmetricSystemURI"] = _to_wiki_url(uri)

                parts = val.get("hasPart", [])
                if isinstance(parts, list) and parts:
                    part_uris = []
                    for part in parts:
                        if isinstance(part, str) and part.strip():
                            uri = get_wikidata_entity_cross_encoder(
                                part,
                                context=pred.get("definition", ""),
                                threshold=threshold,
                            )
                            part_uris.append(_to_wiki_url(uri) if uri else None)
                        else:
                            part_uris.append(None)

                    if any(part_uris):
                        val["hasPartURIs"] = part_uris

    return out


# ======================================================================================
# JSON -> TTL
# ======================================================================================

PREFIXES = """@prefix iop: <https://w3id.org/iadopt/ont/> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix ex: <http://example.org/iadopt/> .
@prefix iopp: <https://w3id.org/iadopt/pattern/> .
@prefix patt: <http://example.org/iadopt/pattern> .
@prefix sosa: <http://www.w3.org/ns/sosa/> .
@prefix uom: <http://www.ontology-of-units-of-measure.org/resource/om-2/> .

"""

WIKIDATA_ENTITY = "https://www.wikidata.org/entity/"


def wiki_to_entity(uri: Optional[str]) -> Optional[str]:
    if not uri:
        return None
    m = re.search(r"(Q\d+)", uri)
    if not m:
        return None
    return WIKIDATA_ENTITY + m.group(1)


def _camel_name_from_label(label: str) -> str:
    label = (label or "").strip()
    if not label:
        return "Variable"

    tokens = re.findall(r"[A-Za-z0-9]+", label)
    if not tokens:
        return "Variable"

    stop = {"of", "the", "and", "in", "on", "at", "for", "to", "a", "an"}
    cleaned = [t for t in tokens if t.lower() not in stop]

    if len(cleaned) >= 3:
        drop = {"spectral", "upwelling", "downwelling", "vertical", "horizontal"}
        if cleaned[0].lower() in drop:
            cleaned = cleaned[1:]

    return "".join(t[:1].upper() + t[1:] for t in cleaned)


def _ttl_quote_multiline(text: str) -> str:
    text = text or ""
    text = text.replace('"""', '\\"""')
    return f'"""{text}"""'


def _entity_block(uri: str, rdf_type: str, label: str) -> str:
    return f"""<{uri}>
    a 
        {rdf_type} ;
    rdfs:label 
        "{label}" .

"""


def json_to_ttl_repo_style(
    pred: Dict[str, Any],
    *,
    issue_url: Optional[str] = None,
    ex_base: str = "http://example.org/iadopt/",
) -> str:
    label = (pred.get("label") or "").strip()
    definition = (pred.get("definition") or "").strip()
    comment = (pred.get("comment") or "").strip()

    if not label:
        label = "Generated Variable"

    var_name = _camel_name_from_label(label)
    var_subject = f"ex:{var_name}"

    components: Dict[str, Tuple[str, str, str]] = {}

    def add_component(field: str, rdf_type: str) -> Optional[str]:
        val = pred.get(field)
        if not isinstance(val, str) or not val.strip():
            return None

        val = val.strip()
        uri_field = f"{field}URI"
        wd_entity = wiki_to_entity(pred.get(uri_field))

        if wd_entity:
            uri = wd_entity
        else:
            local = _camel_name_from_label(val)
            uri = ex_base.rstrip("/") + "/" + local

        components[field] = (uri, rdf_type, val)
        return uri

    prop_uri = add_component("hasProperty", "iop:Property")
    ooi_uri = add_component("hasObjectOfInterest", "iop:Entity")
    mat_uri = add_component("hasMatrix", "iop:Entity")
    ctx_uri = add_component("hasContextObject", "iop:Entity")
    stat_uri = add_component("hasStatisticalModifier", "iop:StatisticalModifier")

    label_to_uri = {components[k][2].lower(): components[k][0] for k in components}

    def resolve_on_target(on_text: str) -> str:
        on_text = (on_text or "").strip()
        if not on_text:
            return var_subject

        on_clean = re.sub(r"^\s*[A-Za-z][A-Za-z0-9_]*\s*:\s*", "", on_text).strip()

        if on_text.lower() in label_to_uri:
            return f"<{label_to_uri[on_text.lower()]}>"
        if on_clean.lower() in label_to_uri:
            return f"<{label_to_uri[on_clean.lower()]}>"

        m = re.search(r"(Q\d+)", on_text)
        if m:
            return f"<{WIKIDATA_ENTITY}{m.group(1)}>"

        if on_text.startswith("http://") or on_text.startswith("https://"):
            wd = wiki_to_entity(on_text)
            return f"<{wd}>" if wd else f"<{on_text}>"

        return var_subject

    constraints = pred.get("hasConstraint") or []
    constraint_blocks: List[str] = []

    if isinstance(constraints, list):
        for c in constraints:
            if not isinstance(c, dict):
                continue

            c_label = (c.get("label") or "").strip()
            c_on = (c.get("on") or "").strip()

            if not c_label:
                continue

            target = resolve_on_target(c_on)

            constraint_blocks.append(
                f"""[ a iop:Constraint ;
             rdfs:label "{c_label}" ;
             iop:constrains {target} ;
        ]"""
            )

    if constraint_blocks:
        joined = " ,\n        ".join(constraint_blocks)
        has_constraint_line = f"    iop:hasConstraint \n        {joined} .\n"
    else:
        has_constraint_line = "    .\n"

    var_lines = [
        f"{var_subject}",
        "    a ",
        "        iop:Variable ;",
        "    rdfs:label ",
        f'        "{label}" ;',
        "    skos:definition ",
        f"        {_ttl_quote_multiline(definition)} ;",
        "    rdfs:comment ",
        f"        {_ttl_quote_multiline(comment)} ;",
    ]

    if issue_url:
        var_lines.append(f'    ex:issue "{issue_url}" ;')

    if ooi_uri:
        var_lines.append(f"    iop:hasObjectOfInterest \n        <{ooi_uri}> ;")
    if mat_uri:
        var_lines.append(f"    iop:hasMatrix \n        <{mat_uri}> ;")
    if prop_uri:
        var_lines.append(f"    iop:hasProperty \n        <{prop_uri}> ;")
    if ctx_uri:
        var_lines.append(f"    iop:hasContextObject \n        <{ctx_uri}> ;")
    if stat_uri:
        var_lines.append(f"    iop:hasStatisticalModifier \n        <{stat_uri}> ;")

    var_block = "\n".join(var_lines) + "\n" + has_constraint_line + "\n"

    bottom = ""
    for field in ["hasObjectOfInterest", "hasMatrix", "hasProperty", "hasContextObject", "hasStatisticalModifier"]:
        if field in components:
            uri, rdf_type, lbl = components[field]
            bottom += _entity_block(uri, rdf_type, lbl)

    return PREFIXES + var_block + bottom


# ======================================================================================
# Main pipeline
# ======================================================================================


def run_pipeline(definition: str) -> Dict[str, Any]:
    definition = definition.strip()
    if not definition:
        raise ValueError("Definition must not be empty.")

    prompt_versions = list_prompt_versions(PROMPT_DIR)
    if not prompt_versions:
        raise RuntimeError(f"No prompt files found in: {PROMPT_DIR}")

    prompt_version = prompt_versions[0]
    examples_5 = load_examples(FIVE_SHOT_DIR, 5)
    prompt = build_prompt(definition, prompt_version=prompt_version, examples=examples_5)

    raw_llm_output, pred = call_llm_loose(
        MODEL_NAME,
        prompt,
        definition=definition,
        temperature=TEMPERATURE,
    )

    if not pred:
        raise RuntimeError("Could not extract valid JSON from the model output.")

    validation_errors = get_schema_validation_errors(pred, label_for_logs=pred.get("label"))
    schema_valid = len(validation_errors) == 0

    if ENABLE_WIKIDATA_LINKING:
        try:
            enriched = enrich_with_uris_cross_encoder(pred, threshold=RERANK_THRESHOLD)
        except Exception as e:
            print(f"Wikidata enrichment failed: {e}")
            enriched = pred
    else:
        enriched = pred

    ttl = json_to_ttl_repo_style(enriched)

    return {
        "raw_llm_output": raw_llm_output,
        "parsed_json": pred,
        "schema_valid": schema_valid,
        "validation_errors": validation_errors,
        "enriched_json": enriched,
        "ttl": ttl,
    }


# ======================================================================================
# Routes
# ======================================================================================


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "schema_exists": SCHEMA_PATH.exists(),
        "prompt_dir_exists": PROMPT_DIR.exists(),
        "five_shot_dir_exists": FIVE_SHOT_DIR.exists(),
        "openrouter_key_set": bool(OPENROUTER_API_KEY),
        "wikidata_linking_enabled": ENABLE_WIKIDATA_LINKING,
    }


@app.post("/decompose", response_model=DecomposeResponse)
def decompose(req: DecomposeRequest) -> DecomposeResponse:
    try:
        result = run_pipeline(req.definition)
        return DecomposeResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected backend error: {e}") from e
