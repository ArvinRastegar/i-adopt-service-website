from __future__ import annotations

import copy
import json
import os
import pathlib
import random
import re
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
import httpx
from nanopub import Nanopub, NanopubConf, Profile
from nanopub.namespaces import NPX, NTEMPLATE, PAV
import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from jsonschema import Draft202012Validator
from openai import APIStatusError, OpenAI, OpenAIError
from pydantic import BaseModel, Field
from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import DCTERMS, FOAF, PROV, RDF, RDFS, SKOS, XSD
from sentence_transformers import CrossEncoder
from contextlib import asynccontextmanager


# ======================================================================================
# App setup
# ======================================================================================
def warmup_assets() -> None:
    global _schema_cache, _validator_cache, _prompt_version_cache, _examples_5_cache

    # OpenRouter client init
    get_openai_client()

    # Heavy model load (do this at startup)
    if ENABLE_WIKIDATA_LINKING:
        get_reranker()

    # Cache schema validator
    _schema_cache = _patch_schema_for_pipeline(load_schema(SCHEMA_PATH))
    _validator_cache = Draft202012Validator(_schema_cache)

    # Cache prompt version + examples
    versions = list_prompt_versions(PROMPT_DIR)
    if not versions:
        raise RuntimeError(f"No prompt files found in: {PROMPT_DIR}")
    _prompt_version_cache = versions[0]
    _examples_5_cache = load_examples(FIVE_SHOT_DIR, 5)

    # Prime HTTP session
    get_http_session()


@asynccontextmanager
async def lifespan(_: FastAPI):
    warmup_assets()
    yield


app = FastAPI(
    title="I-ADOPT Variable Decomposition API",
    version="0.1.0",
    lifespan=lifespan,
)

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

# MODEL_NAME = os.getenv("MODEL_NAME", "qwen/qwen3.5-397b-a17b")
MODEL_NAME = os.getenv("MODEL_NAME", "qwen/qwen3.5-flash-02-23")
# MODEL_NAME = os.getenv("MODEL_NAME", "qwen/qwen3-32b")
# MODEL_NAME = os.getenv("MODEL_NAME", "google/gemini-3-flash-preview")

TEMPERATURE = float(os.getenv("TEMPERATURE", "0.5"))
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
NANOPUB_PRIVATE_KEY = os.getenv("NANOPUB_PRIVATE_KEY")
NANOPUB_PUBLIC_KEY = os.getenv("NANOPUB_PUBLIC_KEY")
NANOPUB_ORCID_ID = os.getenv("NANOPUB_ORCID_ID")
NANOPUB_PROFILE_NAME = os.getenv("NANOPUB_PROFILE_NAME")
NANOPUB_AGENT_INTRO_URI = os.getenv("NANOPUB_AGENT_INTRO_URI")
NANOPUB_PUBLISH_SERVER = os.getenv("NANOPUB_PUBLISH_SERVER", "https://registry.petapico.org/np/")
NANOPUB_LICENSE_URI = os.getenv("NANOPUB_LICENSE_URI", "https://creativecommons.org/licenses/by/4.0/")
NANOPUB_WAS_CREATED_AT = os.getenv("NANOPUB_WAS_CREATED_AT", "https://nanodash.knowledgepixels.com/")
NANOPUB_TEMPLATE_URI = os.getenv(
    "NANOPUB_TEMPLATE_URI", "https://w3id.org/np/RAkcfj9W_lJjlq26paIFmTY4mZoaY27BnZCjcsL34EPIA"
)
NANOPUB_PROVENANCE_TEMPLATE_URI = os.getenv(
    "NANOPUB_PROVENANCE_TEMPLATE_URI", "https://w3id.org/np/RANwQa4ICWS5SOjw7gp99nBpXBasapwtZF1fIM3H2gYTM"
)
NANOPUB_PUBINFO_TEMPLATE_URIS = [
    uri.strip()
    for uri in os.getenv(
        "NANOPUB_PUBINFO_TEMPLATE_URIS",
        "https://w3id.org/np/RAA2MfqdBCzmz9yVWjKLXNbyfBNcwsMmOqcNUxkk1maIM,"
        "https://w3id.org/np/RA0J4vUn_dekg-U1kK3AOEt02p9mT2WO03uGxLDec1jLw,"
        "https://w3id.org/np/RAukAcWHRDlkqxk7H2XNSegc1WnHI569INvNr-xdptDGI",
    ).split(",")
    if uri.strip()
]
IADOPT_VARIABLE_CONFORMS_TO = os.getenv(
    "IADOPT_VARIABLE_CONFORMS_TO",
    "https://nanodash.knowledgepixels.com/explore?id=RA5MTl9GFH-QuuBHYEA2hOtxOMOV4-jrhtdx5lOy9CAQE",
)
NANOPUB_RETRACT_TEMPLATE_URI = os.getenv(
    "NANOPUB_RETRACT_TEMPLATE_URI",
    "https://w3id.org/np/RAQP3NJvnLA2Z-2DrYAN0nTC-RFp67td1t4-pQqQ_ZKmo",
)
NANOPUB_RETRACT_PROVENANCE_TEMPLATE_URI = os.getenv(
    "NANOPUB_RETRACT_PROVENANCE_TEMPLATE_URI",
    "https://w3id.org/np/RA7lSq6MuK_TIC6JMSHvLtee3lpLoZDOqLJCLXevnrPoU",
)
NANOPUB_RETRACT_PUBINFO_TEMPLATE_URIS = [
    uri.strip()
    for uri in os.getenv(
        "NANOPUB_RETRACT_PUBINFO_TEMPLATE_URIS",
        "https://w3id.org/np/RA0J4vUn_dekg-U1kK3AOEt02p9mT2WO03uGxLDec1jLw,"
        "https://w3id.org/np/RAukAcWHRDlkqxk7H2XNSegc1WnHI569INvNr-xdptDGI",
    ).split(",")
    if uri.strip()
]
# IADOPT_CREATED_WITH_LABEL = os.getenv(
#     "IADOPT_CREATED_WITH_LABEL",
#     "LLM-assisted I-ADOPT variable generation",
# )

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


class PublishNanopubRequest(BaseModel):
    ttl: str = Field(..., min_length=1, description="TTL assertion payload currently shown in the frontend")


class PublishNanopubResponse(BaseModel):
    nanopub_url: str
    published_to: str
    variable_identifier: str
    variable_uri: str


class RetractNanopubRequest(BaseModel):
    nanopub_uri: str = Field(
        ..., min_length=1, description="The published nanopub URI or Nanodash explore URL to retract"
    )


class RetractNanopubResponse(BaseModel):
    retraction_url: str
    published_to: str
    retracted_nanopub_url: str


# ======================================================================================
# Lazy-loaded clients/models
# ======================================================================================

_openai_client: Optional[OpenAI] = None
_reranker: Optional[CrossEncoder] = None
_nanopub_profile: Optional[Profile] = None
_nanopub_agent_uri_cache: Optional[str] = None


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


_openai_client: Optional[OpenAI] = None
_reranker: Optional[CrossEncoder] = None
_http_session: Optional[requests.Session] = None

_schema_cache: Optional[Dict[str, Any]] = None
_validator_cache: Optional[Draft202012Validator] = None
_prompt_version_cache: Optional[str] = None
_examples_5_cache: Optional[List[Dict[str, Any]]] = None


def _normalize_env_multiline(value: Optional[str]) -> Optional[str]:
    """Turn `\\n` escapes in `.env` values back into literal newlines before key normalization."""
    if value is None:
        return None
    return value.strip().replace("\\n", "\n")


def _normalize_nanopub_key(value: Optional[str]) -> Optional[str]:
    """Accept PEM blocks or base64 key bodies and normalize them to the base64 form expected by `nanopub-py`."""
    normalized = _normalize_env_multiline(value)
    if not normalized:
        return None

    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    if not lines:
        return None

    if lines[0].startswith("-----BEGIN ") and lines[-1].startswith("-----END "):
        lines = lines[1:-1]

    return "".join(lines)


def _normalize_orcid(orcid_id: Optional[str]) -> Optional[str]:
    if not orcid_id:
        return None
    if orcid_id.startswith("http://") or orcid_id.startswith("https://"):
        return orcid_id
    return f"https://orcid.org/{orcid_id}"


def _orcid_suffix(orcid_id: Optional[str]) -> Optional[str]:
    """Keep the prefix form stable in TTL by extracting the bare ORCID identifier from a full URI."""
    normalized = _normalize_orcid(orcid_id)
    if not normalized:
        return None
    return normalized.rstrip("/").rsplit("/", 1)[-1]


def get_nanopub_profile() -> Profile:
    """Load the signing profile from `.env` so backend publication never depends on frontend secrets."""
    global _nanopub_profile

    if _nanopub_profile is None:
        missing = []
        if not NANOPUB_PRIVATE_KEY:
            missing.append("NANOPUB_PRIVATE_KEY")
        if not NANOPUB_ORCID_ID:
            missing.append("NANOPUB_ORCID_ID")
        if not NANOPUB_PROFILE_NAME:
            missing.append("NANOPUB_PROFILE_NAME")
        if missing:
            raise RuntimeError(f"Missing nanopub publishing configuration: {', '.join(missing)}")

        _nanopub_profile = Profile(
            orcid_id=_normalize_orcid(NANOPUB_ORCID_ID),
            name=NANOPUB_PROFILE_NAME,
            private_key=_normalize_nanopub_key(NANOPUB_PRIVATE_KEY),
            public_key=_normalize_nanopub_key(NANOPUB_PUBLIC_KEY),
        )

    return _nanopub_profile


def get_nanopub_agent_uri() -> Optional[str]:
    """Resolve the software-agent concept URI from its introduction nanopub once and cache it for reuse."""
    global _nanopub_agent_uri_cache

    if _nanopub_agent_uri_cache:
        return _nanopub_agent_uri_cache

    if not NANOPUB_AGENT_INTRO_URI:
        return None

    intro_nanopub = Nanopub(source_uri=NANOPUB_AGENT_INTRO_URI)
    introduced_concept = intro_nanopub.introduces_concept
    if introduced_concept is None:
        raise RuntimeError(
            "Configured NANOPUB_AGENT_INTRO_URI does not introduce a concept. "
            "Provide a valid introduction nanopub for the software agent."
        )

    _nanopub_agent_uri_cache = str(introduced_concept)
    return _nanopub_agent_uri_cache


def get_http_session() -> requests.Session:
    global _http_session
    if _http_session is None:
        _http_session = requests.Session()
        _http_session.headers.update({"User-Agent": "IADOPT-Linker/1.0 (+fastapi)"})
    return _http_session


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
                extra_body={
                    "reasoning": {
                        "effort": "none",  # or: "minimal", "low", "medium", "high"
                    }
                },
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
    if schema is not None:
        validator = Draft202012Validator(_patch_schema_for_pipeline(schema))
    elif _validator_cache is not None:
        validator = _validator_cache
    else:
        schema = _patch_schema_for_pipeline(load_schema(schema_path))
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
    # headers = {"User-Agent": "IADOPT-Linker/1.0 (+fastapi)"}
    url = "https://www.wikidata.org/w/api.php" f"?action=wbsearchentities&search={encoded}&language=en&format=json"

    response = get_http_session().get(url, timeout=20)

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
                # Link both system-level and component-level asymmetric system labels so the serializer
                # can emit readable labels and URIs for all formula variants.
                for kk in ["AsymmetricSystem", "hasSource", "hasTarget", "hasNumerator", "hasDenominator"]:
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

TTL_PREFIXES = """@prefix iop: <https://w3id.org/iadopt/ont/> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix dct: <http://purl.org/dc/terms/> .
@prefix pav: <http://purl.org/pav/> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix orcid: <https://orcid.org/> .
@prefix fdof: <https://w3id.org/fdof/ontology#> .

"""

WIKIDATA_ENTITY = "https://www.wikidata.org/entity/"
IADOPT_VARIABLE_BASE = "https://w3id.org/iadopt/variable/"


def wiki_to_entity(uri: Optional[str]) -> Optional[str]:
    """Normalize Wikidata page URLs into entity URLs so the TTL always points at the canonical resource."""
    if not uri:
        return None
    m = re.search(r"(Q\d+)", uri)
    if not m:
        return None
    return WIKIDATA_ENTITY + m.group(1)


def _ttl_quote(text: str) -> str:
    """Escape arbitrary text once so labels, comments, and definitions stay valid Turtle literals."""
    return json.dumps((text or "").strip(), ensure_ascii=False)


def _normalize_text(text: str) -> str:
    """Collapse repeated whitespace so generated labels read naturally and consistently."""
    return re.sub(r"\s+", " ", (text or "").strip())


def _lookup_key(text: str) -> str:
    """Normalize label lookups so constraints can resolve targets by human-readable names."""
    return _normalize_text(text).lower()


def _make_variable_identity() -> Tuple[str, str, str]:
    """Create the new variable URI, its textual identifier, and the UTC timestamp literal from one clock read."""
    created_at = datetime.now(timezone.utc).replace(microsecond=0)
    identifier_suffix = f"{created_at.strftime('%Y%m%dT%H%M%S')}-{random.randint(0, 99):02d}"
    variable_uri = f"{IADOPT_VARIABLE_BASE}{identifier_suffix}"
    variable_identifier = f"iadopt-variable-{identifier_suffix}"
    created_literal = created_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    return variable_uri, variable_identifier, created_literal


def _format_main_label(pref_label: str) -> str:
    """Promote the LLM label into a human-readable main label while preserving the original wording."""
    pref_label = _normalize_text(pref_label)
    if not pref_label:
        return "Generated variable"
    return pref_label[:1].upper() + pref_label[1:]


def _make_comment(formula_name: str) -> str:
    """Explain directly in the TTL how the preferred and alternative labels were produced."""
    return (
        "LLM-proposed preferred label is stored in skos:prefLabel. "
        f"The alternative label is generated from the {formula_name} formula."
    )


def _literal_join(parts: List[str]) -> str:
    """Join only the non-empty text fragments and normalize the result for use as a label phrase."""
    return _normalize_text(" ".join(part for part in parts if part))


def _phrase_for_role(role: str, label: str, constraints_by_role: Dict[str, List[str]]) -> str:
    """Place qualifier text before properties/modifiers and after entities so labels stay readable."""
    clean_label = _normalize_text(label)
    if not clean_label:
        return ""

    clean_constraints = [_normalize_text(item) for item in constraints_by_role.get(role, []) if _normalize_text(item)]
    if not clean_constraints:
        return clean_label

    constraint_text = " ".join(clean_constraints)
    if role in {"property", "statistical_modifier"}:
        return _literal_join([constraint_text, clean_label])
    return _literal_join([clean_label, constraint_text])


def _build_alt_label(formula_context: Dict[str, str], constraints_by_role: Dict[str, List[str]]) -> Tuple[str, str]:
    """Select the matching label formula and assemble the final `skos:altLabel` text."""
    uses_ooi_asymmetric = formula_context.get("ooi_kind") == "asymmetric"
    uses_matrix_asymmetric = formula_context.get("matrix_kind") == "asymmetric"

    if uses_ooi_asymmetric and formula_context.get("numerator") and formula_context.get("denominator"):
        formula_name = "asymmetric-numerator-denominator"
        phrase_plan = [
            (
                _phrase_for_role(
                    "statistical_modifier", formula_context.get("statistical_modifier", ""), constraints_by_role
                ),
                None,
            ),
            (_phrase_for_role("property", formula_context.get("property", ""), constraints_by_role), None),
            (_phrase_for_role("numerator", formula_context.get("numerator", ""), constraints_by_role), "of"),
            (_phrase_for_role("denominator", formula_context.get("denominator", ""), constraints_by_role), "in"),
            (_phrase_for_role("matrix", formula_context.get("matrix", ""), constraints_by_role), "in"),
            (_phrase_for_role("context", formula_context.get("context", ""), constraints_by_role), "in"),
        ]
    elif uses_ooi_asymmetric and formula_context.get("source") and formula_context.get("target"):
        formula_name = "asymmetric-source-target-object"
        phrase_plan = [
            (
                _phrase_for_role(
                    "statistical_modifier", formula_context.get("statistical_modifier", ""), constraints_by_role
                ),
                None,
            ),
            (_phrase_for_role("property", formula_context.get("property", ""), constraints_by_role), None),
            (_phrase_for_role("source", formula_context.get("source", ""), constraints_by_role), "from"),
            (_phrase_for_role("target", formula_context.get("target", ""), constraints_by_role), "to"),
            (_phrase_for_role("matrix", formula_context.get("matrix", ""), constraints_by_role), "in"),
            (_phrase_for_role("context", formula_context.get("context", ""), constraints_by_role), "in"),
        ]
    elif uses_matrix_asymmetric and formula_context.get("source") and formula_context.get("target"):
        formula_name = "asymmetric-source-target-matrix"
        phrase_plan = [
            (
                _phrase_for_role(
                    "statistical_modifier", formula_context.get("statistical_modifier", ""), constraints_by_role
                ),
                None,
            ),
            (_phrase_for_role("property", formula_context.get("property", ""), constraints_by_role), None),
            (_phrase_for_role("object", formula_context.get("object", ""), constraints_by_role), "of"),
            (_phrase_for_role("source", formula_context.get("source", ""), constraints_by_role), "from"),
            (_phrase_for_role("target", formula_context.get("target", ""), constraints_by_role), "to"),
            (_phrase_for_role("context", formula_context.get("context", ""), constraints_by_role), "in"),
        ]
    else:
        formula_name = "simple-entity"
        phrase_plan = [
            (
                _phrase_for_role(
                    "statistical_modifier", formula_context.get("statistical_modifier", ""), constraints_by_role
                ),
                None,
            ),
            (_phrase_for_role("property", formula_context.get("property", ""), constraints_by_role), None),
            (_phrase_for_role("object", formula_context.get("object", ""), constraints_by_role), "of"),
            (_phrase_for_role("matrix", formula_context.get("matrix", ""), constraints_by_role), "in"),
            (_phrase_for_role("context", formula_context.get("context", ""), constraints_by_role), "in"),
        ]

    assembled: List[str] = []
    for phrase, connector in phrase_plan:
        if not phrase:
            continue
        if connector and assembled:
            assembled.append(connector)
        assembled.append(phrase)

    alt_label = _literal_join(assembled)
    return alt_label or _normalize_text(formula_context.get("pref_label", "")), formula_name


def json_to_ttl_repo_style(pred: Dict[str, Any]) -> str:
    """Serialize the enriched JSON prediction into the new simple I-ADOPT TTL shape required by the frontend."""
    pref_label = _normalize_text(pred.get("label") or "generated variable")
    main_label = _format_main_label(pref_label)
    definition = _normalize_text(pred.get("definition") or "")
    variable_uri, variable_identifier, created_literal = _make_variable_identity()
    orcid_suffix = _orcid_suffix(NANOPUB_ORCID_ID) or "0000-0000-0000-0000"

    blocks: List[str] = []
    variable_lines: List[str] = []
    constraint_targets: Dict[str, Tuple[str, str]] = {}
    constraints_by_role: Dict[str, List[str]] = {}
    formula_context: Dict[str, str] = {
        "pref_label": pref_label,
        "ooi_kind": "simple",
        "matrix_kind": "simple",
    }

    def local_resource_ref(suffix: str) -> str:
        return f"<{variable_uri}#{suffix}>"

    def register_target(ref: str, role: str, *aliases: Optional[str]) -> None:
        # This lookup table lets constraint `on` values resolve against either field names or human-readable labels.
        for alias in aliases:
            if alias:
                constraint_targets[_lookup_key(alias)] = (ref, role)

    def add_block(
        ref: str, rdf_types: List[str], label: Optional[str], extra_lines: Optional[List[str]] = None
    ) -> None:
        # Every linked resource gets its own readable TTL block so the frontend receives a self-contained graph.
        lines = [f"{ref}", "    a " + " ,\n      ".join(rdf_types) + " ;"]
        if label:
            lines.append(f"    rdfs:label {_ttl_quote(label)} ;")
        for extra_line in extra_lines or []:
            lines.append(extra_line)
        # Close the block by replacing the last semicolon with a final period.
        lines[-1] = lines[-1].rstrip(" ;") + " ."
        blocks.append("\n".join(lines))

    def build_simple_component(field: str, label: str, rdf_type: str, uri_override: Optional[str]) -> Tuple[str, str]:
        clean_label = _normalize_text(label)
        ref = f"<{uri_override}>" if uri_override else local_resource_ref(field)
        add_block(ref, [rdf_type], clean_label)
        return ref, clean_label

    def build_system_component(field: str, value: Dict[str, Any], role_name: str) -> Tuple[str, str]:
        system_key = "AsymmetricSystem" if "AsymmetricSystem" in value else "SymmetricSystem"
        system_label = _normalize_text(value.get(system_key) or field)
        system_uri = wiki_to_entity(value.get(f"{system_key}URI"))
        system_ref = f"<{system_uri}>" if system_uri else local_resource_ref(field)
        component_lines: List[str] = []
        kind_key = "ooi_kind" if role_name == "object" else "matrix_kind" if role_name == "matrix" else f"{field}_kind"

        if system_key == "AsymmetricSystem":
            # Source/target and numerator/denominator resources are emitted explicitly so constraints
            # and alt-label formulas can target them individually.
            formula_context[kind_key] = "asymmetric"
            asym_roles = [
                ("hasSource", "source", f"{field}-source"),
                ("hasTarget", "target", f"{field}-target"),
                ("hasNumerator", "numerator", f"{field}-numerator"),
                ("hasDenominator", "denominator", f"{field}-denominator"),
            ]
            for key, role_name, suffix in asym_roles:
                role_label = _normalize_text(value.get(key) or "")
                if not role_label:
                    continue
                role_uri = wiki_to_entity(value.get(f"{key}URI"))
                role_ref, clean_role_label = build_simple_component(suffix, role_label, "iop:Entity", role_uri)
                component_lines.append(f"    iop:{key} {role_ref} ;")
                formula_context[role_name] = clean_role_label
                register_target(role_ref, role_name, key, role_name, clean_role_label)

            add_block(system_ref, ["iop:Entity", "iop:AsymmetricSystem"], system_label, component_lines)
        else:
            formula_context[kind_key] = "symmetric"
            part_refs: List[str] = []
            part_uris = value.get("hasPartURIs") if isinstance(value.get("hasPartURIs"), list) else []
            for idx, part_label in enumerate(value.get("hasPart") or [], start=1):
                clean_part_label = _normalize_text(part_label)
                if not clean_part_label:
                    continue
                part_uri = wiki_to_entity(part_uris[idx - 1]) if idx - 1 < len(part_uris) else None
                part_ref, _ = build_simple_component(f"{field}-part-{idx}", clean_part_label, "iop:Entity", part_uri)
                part_refs.append(part_ref)
                register_target(part_ref, f"{field}_part", clean_part_label)

            if part_refs:
                component_lines.append(f"    iop:hasPart {', '.join(part_refs)} ;")
            add_block(system_ref, ["iop:Entity", "iop:SymmetricSystem"], system_label, component_lines)

        return system_ref, system_label

    def build_component(field: str, rdf_type: str, role_name: str) -> Tuple[Optional[str], str]:
        # This one function keeps the simple-entity and system cases aligned so later label
        # generation and constraint resolution work from the same canonical context.
        value = pred.get(field)
        if isinstance(value, str) and _normalize_text(value):
            uri = wiki_to_entity(pred.get(f"{field}URI"))
            ref, label = build_simple_component(field, value, rdf_type, uri)
            formula_context[role_name] = label
            register_target(ref, role_name, field, role_name, label)
            return ref, label

        if isinstance(value, dict):
            ref, label = build_system_component(field, value, role_name)
            formula_context[role_name] = label
            register_target(
                ref, role_name, field, role_name, label, value.get("AsymmetricSystem"), value.get("SymmetricSystem")
            )
            return ref, label

        return None, ""

    property_ref, _ = build_component("hasProperty", "iop:Property", "property")
    stat_ref, _ = build_component("hasStatisticalModifier", "iop:StatisticalModifier", "statistical_modifier")
    ooi_ref, _ = build_component("hasObjectOfInterest", "iop:Entity", "object")
    matrix_ref, _ = build_component("hasMatrix", "iop:Entity", "matrix")
    context_ref, _ = build_component("hasContextObject", "iop:Entity", "context")

    constraint_refs: List[str] = []
    for idx, constraint in enumerate(pred.get("hasConstraint") or [], start=1):
        if not isinstance(constraint, dict):
            continue

        constraint_label = _normalize_text(constraint.get("label") or "")
        constraint_on = _lookup_key(constraint.get("on") or "")
        if not constraint_label or not constraint_on:
            continue

        target_ref, target_role = constraint_targets.get(constraint_on, (f"<{variable_uri}>", "variable"))
        constraints_by_role.setdefault(target_role, []).append(constraint_label)
        constraint_ref = f"_:c{idx}"
        constraint_refs.append(constraint_ref)
        blocks.append(
            "\n".join(
                [
                    f"{constraint_ref}",
                    "    a iop:Constraint ;",
                    f"    rdfs:label {_ttl_quote(constraint_label)} ;",
                    f"    iop:constrains {target_ref} .",
                ]
            )
        )

    alt_label, formula_name = _build_alt_label(formula_context, constraints_by_role)

    variable_lines.extend(
        [
            f"<{variable_uri}>",
            "    a fdof:FAIRDigitalObject ,",
            "      iop:Variable ;",
            f"    dct:conformsTo <{IADOPT_VARIABLE_CONFORMS_TO}> ;",
            f"    rdfs:label {_ttl_quote(main_label)} ;",
            f"    skos:prefLabel {_ttl_quote(pref_label)} ;",
            f"    skos:altLabel {_ttl_quote(alt_label)} ;",
            f"    skos:definition {_ttl_quote(definition)} ;",
            f"    rdfs:comment {_ttl_quote(_make_comment(formula_name))} ;",
            f"    dct:identifier {_ttl_quote(variable_identifier)} ;",
            # f'    dct:created "{created_literal}"^^xsd:dateTime ;',
            # f"    dct:creator orcid:{orcid_suffix} ;",
            # f"    pav:createdWith {_ttl_quote(IADOPT_CREATED_WITH_LABEL)} ;",
            # f"    prov:wasAttributedTo orcid:{orcid_suffix} ;",
        ]
    )

    if ooi_ref:
        variable_lines.append(f"    iop:hasObjectOfInterest {ooi_ref} ;")
    if property_ref:
        variable_lines.append(f"    iop:hasProperty {property_ref} ;")
    if matrix_ref:
        variable_lines.append(f"    iop:hasMatrix {matrix_ref} ;")
    if context_ref:
        variable_lines.append(f"    iop:hasContextObject {context_ref} ;")
    if stat_ref:
        variable_lines.append(f"    iop:hasStatisticalModifier {stat_ref} ;")
    if constraint_refs:
        variable_lines.append(f"    iop:hasConstraint {', '.join(constraint_refs)} ;")

    variable_lines[-1] = variable_lines[-1].rstrip(" ;") + " ."

    creator_block = "\n".join(
        [
            f"orcid:{orcid_suffix}",
            f"    rdfs:label {_ttl_quote(NANOPUB_PROFILE_NAME or 'Unknown creator')} .",
        ]
    )

    return "\n".join([TTL_PREFIXES, "\n".join(variable_lines), "", *blocks, creator_block, ""])


# ======================================================================================
# Main pipeline
# ======================================================================================


def run_pipeline(definition: str) -> Dict[str, Any]:
    definition = definition.strip()
    if not definition:
        raise ValueError("Definition must not be empty.")

    prompt_version = _prompt_version_cache
    if not prompt_version:
        prompt_versions = list_prompt_versions(PROMPT_DIR)
        if not prompt_versions:
            raise RuntimeError(f"No prompt files found in: {PROMPT_DIR}")
        prompt_version = prompt_versions[0]

    examples_5 = _examples_5_cache if _examples_5_cache is not None else load_examples(FIVE_SHOT_DIR, 5)
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

IADOPT_VARIABLE_CLASS = URIRef("https://w3id.org/iadopt/ont/Variable")


def _nanopub_created_literal() -> Literal:
    """Create the publication timestamp once so every pubinfo timestamp is internally consistent."""
    created_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    return Literal(created_at.replace("+00:00", "Z"), datatype=XSD.dateTime)


def _extract_variable_uri(assertion_graph: Graph) -> URIRef:
    """Find the variable resource in the assertion so pubinfo can point `npx:introduces` at it."""
    for subject in assertion_graph.subjects(RDF.type, IADOPT_VARIABLE_CLASS):
        if isinstance(subject, URIRef):
            return subject

    raise RuntimeError("The Turtle assertion does not contain an `iop:Variable` resource with a URI subject.")


def _extract_assertion_label(assertion_graph: Graph, variable_uri: URIRef) -> Optional[str]:
    """Reuse the variable label as the nanopub label when it exists in the assertion graph."""
    label = assertion_graph.value(variable_uri, RDFS.label)
    if label is None:
        return None
    label_text = str(label).strip()
    return label_text or None


def _extract_variable_identifier(assertion_graph: Graph, variable_uri: URIRef) -> str:
    """Return the variable identifier string that the frontend stores in the retract dropdown."""
    identifier = assertion_graph.value(variable_uri, DCTERMS.identifier)
    if identifier is not None and str(identifier).strip():
        return str(identifier).strip()
    return str(variable_uri).rstrip("/").rsplit("/", 1)[-1]


def _normalize_target_nanopub_uri(raw_value: str) -> str:
    """Accept saved nanopub URLs, raw RA identifiers, or Nanodash explore links and normalize them to the canonical URI."""
    candidate = (raw_value or "").strip()
    if not candidate:
        raise RuntimeError("No nanopub URI was provided for retraction.")

    # Support Nanodash explore URLs such as `.../explore?id=RA...` by extracting the underlying nanopub identifier.
    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme and parsed.netloc:
        query_id = urllib.parse.parse_qs(parsed.query).get("id", [])
        if query_id and query_id[0]:
            candidate = query_id[0].strip()
        else:
            trusty_match = re.search(r"(RA[A-Za-z0-9_-]+)", candidate)
            if trusty_match:
                candidate = trusty_match.group(1)

    if re.fullmatch(r"RA[A-Za-z0-9_-]+", candidate):
        return f"https://w3id.org/np/{candidate}"

    if candidate.startswith("https://w3id.org/np/"):
        return candidate

    raise RuntimeError(
        "Unsupported nanopub reference. Provide a `https://w3id.org/np/RA...` URI, "
        "a raw `RA...` identifier, or a Nanodash explore URL."
    )


def _public_key_prefix(public_key: Optional[str], prefix_length: int = 32) -> str:
    """Shorten public keys in error messages so users can compare them without dumping the full key."""
    clean_key = (public_key or "").strip()
    if not clean_key:
        return "missing"
    return clean_key[:prefix_length]


def _assert_retraction_allowed(target_nanopub_uri: str, profile: Profile) -> None:
    """Enforce the key-match rule ourselves because `nanopub-py`'s local retract check is unreliable."""
    try:
        target_nanopub = Nanopub(
            source_uri=target_nanopub_uri,
            conf=NanopubConf(use_server=NANOPUB_PUBLISH_SERVER),
        )
    except Exception as e:
        raise RuntimeError(f"Could not load the target nanopub for retraction: {e}") from e

    target_public_key = (target_nanopub.metadata.public_key or "").strip()
    profile_public_key = (profile.public_key or "").strip()

    if not target_public_key:
        raise RuntimeError(
            "The target nanopub does not expose a public key, so retraction ownership cannot be verified."
        )

    if not profile_public_key:
        raise RuntimeError("The configured nanopub profile does not expose a public key.")

    if target_public_key != profile_public_key:
        raise RuntimeError(
            "The target nanopub was not signed with the key currently configured in this backend, so it cannot be "
            "retracted here. "
            f"Target key prefix: {_public_key_prefix(target_public_key)} ; "
            f"current key prefix: {_public_key_prefix(profile_public_key)}."
        )


def _build_retraction_nanopub(target_nanopub_uri: str, profile: Profile) -> Nanopub:
    """Create the richer retraction nanopub shape that the production registries currently accept."""
    orcid_uri = URIRef(_normalize_orcid(NANOPUB_ORCID_ID))
    target_identifier = target_nanopub_uri.rsplit("/", 1)[-1]
    retraction_label = f"Retraction of {target_identifier[:10]}"

    assertion_graph = Graph()
    assertion_graph.add((orcid_uri, NPX.retracts, URIRef(target_nanopub_uri)))

    nanopub = Nanopub(
        assertion=assertion_graph,
        conf=NanopubConf(
            profile=profile,
            use_server=NANOPUB_PUBLISH_SERVER,
            add_prov_generated_time=False,
            add_pubinfo_generated_time=False,
            attribute_assertion_to_profile=False,
            attribute_publication_to_profile=False,
        ),
    )

    # The registries accept retractions when they mirror the current Nanodash-style pubinfo shape.
    nanopub.provenance.add((nanopub.assertion.identifier, PROV.wasAttributedTo, orcid_uri))
    nanopub.pubinfo.add((orcid_uri, FOAF.name, Literal(NANOPUB_PROFILE_NAME)))
    nanopub.pubinfo.add((nanopub.metadata.namespace[""], DCTERMS.created, _nanopub_created_literal()))
    nanopub.pubinfo.add((nanopub.metadata.namespace[""], DCTERMS.creator, orcid_uri))
    nanopub.pubinfo.add((nanopub.metadata.namespace[""], DCTERMS.license, URIRef(NANOPUB_LICENSE_URI)))
    nanopub.pubinfo.add((nanopub.metadata.namespace[""], NPX.hasNanopubType, NPX.retracts))
    nanopub.pubinfo.add((nanopub.metadata.namespace[""], NPX["wasCreatedAt"], URIRef(NANOPUB_WAS_CREATED_AT)))
    nanopub.pubinfo.add((nanopub.metadata.namespace[""], RDFS.label, Literal(retraction_label)))

    if NANOPUB_RETRACT_PROVENANCE_TEMPLATE_URI:
        nanopub.pubinfo.add(
            (
                nanopub.metadata.namespace[""],
                NTEMPLATE["wasCreatedFromProvenanceTemplate"],
                URIRef(NANOPUB_RETRACT_PROVENANCE_TEMPLATE_URI),
            )
        )

    for template_uri in NANOPUB_RETRACT_PUBINFO_TEMPLATE_URIS:
        nanopub.pubinfo.add(
            (
                nanopub.metadata.namespace[""],
                NTEMPLATE["wasCreatedFromPubinfoTemplate"],
                URIRef(template_uri),
            )
        )

    if NANOPUB_RETRACT_TEMPLATE_URI:
        nanopub.pubinfo.add(
            (
                nanopub.metadata.namespace[""],
                NTEMPLATE["wasCreatedFromTemplate"],
                URIRef(NANOPUB_RETRACT_TEMPLATE_URI),
            )
        )

    return nanopub


def _add_nanopub_metadata(
    nanopub: Nanopub,
    *,
    variable_uri: URIRef,
    created_at: Literal,
    agent_uri: Optional[str],
) -> None:
    """Mirror the requested provenance and template metadata into the nanopub before signing."""
    nanopub_uri = nanopub.metadata.namespace[""]
    orcid_uri = URIRef(_normalize_orcid(NANOPUB_ORCID_ID))

    # The provenance graph must describe who is responsible for the assertion and which software agent generated it.
    nanopub.provenance.add((nanopub.assertion.identifier, PROV.wasAttributedTo, orcid_uri))
    if agent_uri:
        nanopub.provenance.add((nanopub.assertion.identifier, PROV.wasGeneratedBy, URIRef(agent_uri)))

    # The publication info graph mirrors the creator, license, template, and software metadata requested by the user.
    nanopub.pubinfo.add((orcid_uri, FOAF.name, Literal(NANOPUB_PROFILE_NAME)))
    nanopub.pubinfo.add((nanopub_uri, DCTERMS.created, created_at))
    nanopub.pubinfo.add((nanopub_uri, DCTERMS.creator, orcid_uri))
    nanopub.pubinfo.add((nanopub_uri, DCTERMS.license, URIRef(NANOPUB_LICENSE_URI)))
    nanopub.pubinfo.add((nanopub_uri, NPX.introduces, variable_uri))
    nanopub.pubinfo.add((nanopub_uri, NPX["wasCreatedAt"], URIRef(NANOPUB_WAS_CREATED_AT)))

    # if agent_uri:
    #     nanopub.pubinfo.add((nanopub_uri, PAV.createdWith, URIRef(agent_uri)))

    if NANOPUB_TEMPLATE_URI:
        nanopub.pubinfo.add((nanopub_uri, NTEMPLATE["wasCreatedFromTemplate"], URIRef(NANOPUB_TEMPLATE_URI)))

    if NANOPUB_PROVENANCE_TEMPLATE_URI:
        nanopub.pubinfo.add(
            (
                nanopub_uri,
                NTEMPLATE["wasCreatedFromProvenanceTemplate"],
                URIRef(NANOPUB_PROVENANCE_TEMPLATE_URI),
            )
        )

    for template_uri in NANOPUB_PUBINFO_TEMPLATE_URIS:
        nanopub.pubinfo.add((nanopub_uri, NTEMPLATE["wasCreatedFromPubinfoTemplate"], URIRef(template_uri)))


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "schema_exists": SCHEMA_PATH.exists(),
        "prompt_dir_exists": PROMPT_DIR.exists(),
        "five_shot_dir_exists": FIVE_SHOT_DIR.exists(),
        "openrouter_key_set": bool(OPENROUTER_API_KEY),
        "nanopub_publish_ready": bool(NANOPUB_PRIVATE_KEY and NANOPUB_ORCID_ID and NANOPUB_PROFILE_NAME),
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


@app.post("/nanopub/publish", response_model=PublishNanopubResponse)
def publish_nanopub(req: PublishNanopubRequest) -> PublishNanopubResponse:
    """Publish the exact TTL currently shown in the frontend as a signed nanopublication."""
    ttl = req.ttl.strip()
    if not ttl:
        raise HTTPException(status_code=400, detail="TTL payload is empty.")

    assertion_graph = Graph()
    try:
        assertion_graph.parse(data=ttl, format="turtle")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse Turtle payload: {e}") from e

    try:
        profile = get_nanopub_profile()
        variable_uri = _extract_variable_uri(assertion_graph)
        variable_identifier = _extract_variable_identifier(assertion_graph, variable_uri)
        assertion_label = _extract_assertion_label(assertion_graph, variable_uri)
        created_at = _nanopub_created_literal()
        agent_uri = get_nanopub_agent_uri()

        nanopub_conf = NanopubConf(
            profile=profile,
            use_server=NANOPUB_PUBLISH_SERVER,
            add_prov_generated_time=False,
            add_pubinfo_generated_time=False,
            attribute_assertion_to_profile=False,
            attribute_publication_to_profile=False,
        )
        nanopub = Nanopub(assertion=assertion_graph, conf=nanopub_conf)
        _add_nanopub_metadata(
            nanopub,
            variable_uri=variable_uri,
            created_at=created_at,
            agent_uri=agent_uri,
        )

        if assertion_label:
            # Carry the assertion label into the nanopub pubinfo so the resulting publication is easier to inspect.
            nanopub.pubinfo.add((nanopub.metadata.namespace[""], RDFS.label, Literal(assertion_label)))

        publish_result = nanopub.publish()
        nanopub_url = str(publish_result[0])
        published_to = str(publish_result[1])

        return PublishNanopubResponse(
            nanopub_url=nanopub_url,
            published_to=published_to,
            variable_identifier=variable_identifier,
            variable_uri=str(variable_uri),
        )
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Nanopub publish failed: {e}") from e


@app.post("/nanopub/retract", response_model=RetractNanopubResponse)
def retract_nanopub(req: RetractNanopubRequest) -> RetractNanopubResponse:
    """Publish a signed nanopub retraction for a previously published nanopublication."""
    try:
        target_nanopub_uri = _normalize_target_nanopub_uri(req.nanopub_uri)
        profile = get_nanopub_profile()
        _assert_retraction_allowed(target_nanopub_uri, profile)
        retraction = _build_retraction_nanopub(target_nanopub_uri, profile)

        # Publishing the custom retraction nanopub creates a new nanopub whose assertion retracts the target URI.
        publish_result = retraction.publish()
        return RetractNanopubResponse(
            retraction_url=str(publish_result[0]),
            published_to=str(publish_result[1]),
            retracted_nanopub_url=target_nanopub_uri,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Nanopub retract failed: {e}") from e
