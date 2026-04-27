import json
import logging
import os
import re
import asyncio
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib import error, parse, request

import chromadb
import psycopg2
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sentence_transformers import SentenceTransformer


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("geoint-rag-api")


POSTGRES_HOST = os.getenv("POSTGRES_HOST", "postgis-service")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_DB = os.getenv("POSTGRES_DB", "geoint_db")
POSTGRES_USER = os.getenv("POSTGRES_USER", "geoint_user")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "GeointPass!2026")

CHROMA_HOST = os.getenv("CHROMA_HOST", "chromadb-service")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8000"))
CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION", "geoint_documents")

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_API_ENDPOINT = os.getenv(
    "GEMINI_API_ENDPOINT", "https://generativelanguage.googleapis.com/v1beta"
).rstrip("/")



class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    response: str
    sources: List[Dict[str, Any]]
    coordinates: List[List[float]]


app = FastAPI(title="GEOINT RAG API", version="1.0.0")

embedding_model: Optional[SentenceTransformer] = None
chroma_client = None
chroma_collection = None


def normalize_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def parse_wkt_bbox_coords(wkt: str) -> Optional[List[float]]:
    if not wkt:
        return None
    nums = [float(x) for x in re.findall(r"-?\d+\.?\d*", wkt)]
    if len(nums) < 2:
        return None

    xs = nums[0::2]
    ys = nums[1::2]
    if not xs or not ys:
        return None

    # Return center as [lat, lon]
    center_lon = (min(xs) + max(xs)) / 2.0
    center_lat = (min(ys) + max(ys)) / 2.0
    return [round(center_lat, 6), round(center_lon, 6)]


def fetch_postgis_documents() -> List[Tuple[str, Dict[str, Any]]]:
    logger.info("Connecting to PostGIS at %s:%s/%s", POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB)
    conn = psycopg2.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        dbname=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
    )

    docs: List[Tuple[str, Dict[str, Any]]] = []
    try:
        with conn.cursor() as cur:
            # military_installations
            cur.execute(
                """
                SELECT id, name, type, classification, country,
                       ST_Y(geometry) AS lat,
                       ST_X(geometry) AS lon
                FROM geoint_data.military_installations
                ORDER BY id
                """
            )
            for row in cur.fetchall():
                rid, name, rtype, cls, country, lat, lon = row
                text = (
                    f"Military installation record {rid}: {name} in {country}. "
                    f"Type: {rtype}. Classification: {cls}. Coordinates: [{lat}, {lon}]."
                )
                docs.append(
                    (
                        text,
                        {
                            "source_table": "military_installations",
                            "record_id": str(rid),
                            "classification": cls,
                            "coordinates": json.dumps([float(lat), float(lon)]),
                        },
                    )
                )

            # satellite_imagery_catalog
            cur.execute(
                """
                SELECT id, sensor_name, acquisition_date, cloud_cover,
                       resolution_meters, classification,
                       ST_AsText(footprint) AS footprint_wkt
                FROM geoint_data.satellite_imagery_catalog
                ORDER BY id
                """
            )
            for row in cur.fetchall():
                rid, sensor, acq_date, cloud, res_m, cls, footprint_wkt = row
                center = parse_wkt_bbox_coords(footprint_wkt)
                text = (
                    f"Satellite imagery record {rid}: Sensor {sensor}, acquisition date {normalize_value(acq_date)}, "
                    f"cloud cover {cloud} percent, resolution {res_m} meters. "
                    f"Classification: {cls}. Footprint center coordinates: {center}."
                )
                docs.append(
                    (
                        text,
                        {
                            "source_table": "satellite_imagery_catalog",
                            "record_id": str(rid),
                            "classification": cls,
                            "coordinates": json.dumps(center if center else []),
                        },
                    )
                )

            # geoint_reports
            cur.execute(
                """
                SELECT id, title, summary, report_date, classification,
                       ST_AsText(area_of_interest) AS aoi_wkt
                FROM geoint_data.geoint_reports
                ORDER BY id
                """
            )
            for row in cur.fetchall():
                rid, title, summary, report_date, cls, aoi_wkt = row
                center = parse_wkt_bbox_coords(aoi_wkt)
                text = (
                    f"GEOINT report {rid}: {title}. Summary: {summary} "
                    f"Report date: {normalize_value(report_date)}. Classification: {cls}. "
                    f"Area of interest center coordinates: {center}."
                )
                docs.append(
                    (
                        text,
                        {
                            "source_table": "geoint_reports",
                            "record_id": str(rid),
                            "classification": cls,
                            "coordinates": json.dumps(center if center else []),
                        },
                    )
                )
    finally:
        conn.close()

    logger.info("Fetched %d total documents from PostGIS", len(docs))
    return docs


def init_vector_store() -> None:
    global embedding_model, chroma_client, chroma_collection

    embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
    chroma_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)

    try:
        chroma_collection = chroma_client.get_collection(CHROMA_COLLECTION)
        logger.info("Using existing Chroma collection: %s", CHROMA_COLLECTION)
    except Exception:
        chroma_collection = chroma_client.create_collection(CHROMA_COLLECTION)
        logger.info("Created Chroma collection: %s", CHROMA_COLLECTION)

    docs = fetch_postgis_documents()
    if not docs:
        logger.warning("No documents found in PostGIS; skipping ingest")
        return

    texts = [t for (t, _) in docs]
    metas = [m for (_, m) in docs]
    ids = [f"{m['source_table']}-{m['record_id']}" for m in metas]
    embeddings = embedding_model.encode(texts).tolist()

    # Reset collection for deterministic demo behavior.
    existing = chroma_collection.get(include=[])
    if existing.get("ids"):
        chroma_collection.delete(ids=existing["ids"])

    chroma_collection.add(ids=ids, documents=texts, metadatas=metas, embeddings=embeddings)
    logger.info("Ingested %d documents into Chroma", len(ids))


def extract_coordinates_from_metadata(metadatas: List[Dict[str, Any]]) -> List[List[float]]:
    coords: List[List[float]] = []
    seen = set()
    for meta in metadatas:
        raw = meta.get("coordinates")
        if not raw:
            continue
        try:
            arr = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(arr, list) and len(arr) == 2:
                lat = float(arr[0])
                lon = float(arr[1])
                key = (round(lat, 6), round(lon, 6))
                if key not in seen:
                    seen.add(key)
                    coords.append([lat, lon])
        except Exception:
            continue
    return coords


def build_prompt(retrieved_documents: List[str], user_question: str) -> str:
    context = "\n\n".join(retrieved_documents)
    return f"""
You are a GEOINT analyst assistant. Using the following geospatial intelligence data as context, answer the user's question.
Always cite specific data points and coordinates when relevant.
If the question involves locations, include lat/lon coordinates in your response formatted as [LAT, LON].

Context:
{context}

Question: {user_question}

Provide a professional intelligence-style briefing response.
""".strip()


def extract_gemini_text(payload: Dict[str, Any]) -> str:
    candidates = payload.get("candidates")
    if isinstance(candidates, list):
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            content = candidate.get("content", {})
            if not isinstance(content, dict):
                continue
            parts = content.get("parts")
            if not isinstance(parts, list):
                continue
            text_chunks = [p.get("text", "") for p in parts if isinstance(p, dict)]
            text = "".join(text_chunks).strip()
            if text:
                return text

    return "No response generated."


def invoke_gemini(prompt: str) -> str:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    request_body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 700,
        },
    }

    encoded_model = parse.quote(GEMINI_MODEL, safe="")
    invoke_url = f"{GEMINI_API_ENDPOINT}/models/{encoded_model}:generateContent?key={GEMINI_API_KEY}"
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
    }

    req = request.Request(
        url=invoke_url,
        data=json.dumps(request_body).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=90) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Gemini API request failed with HTTP {exc.code}: {body}") from exc

    return extract_gemini_text(payload)


async def query_gemini(prompt: str) -> str:
    return await asyncio.to_thread(invoke_gemini, prompt)


@app.on_event("startup")
def startup_event() -> None:
    try:
        if not GEMINI_API_KEY:
            logger.warning("GEMINI_API_KEY is not configured; /api/chat calls will fail")
        logger.info("Configured Gemini API endpoint: %s", GEMINI_API_ENDPOINT)
        init_vector_store()
    except Exception as exc:
        logger.exception("Startup initialization failed: %s", exc)


@app.get("/api/health")
def health() -> Dict[str, str]:
    status = "ok" if chroma_collection is not None and embedding_model is not None and bool(GEMINI_API_KEY) else "degraded"
    return {"status": status}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    if chroma_collection is None or embedding_model is None:
        raise HTTPException(status_code=503, detail="RAG backend is not initialized")

    question_embedding = embedding_model.encode([req.message]).tolist()[0]
    results = chroma_collection.query(query_embeddings=[question_embedding], n_results=5)

    docs = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]

    if not docs:
        return ChatResponse(
            response="No relevant GEOINT context was found in the current dataset.",
            sources=[],
            coordinates=[],
        )

    prompt = build_prompt(docs, req.message)

    try:
        model_response = await query_gemini(prompt)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Google Gemini request failed: {exc}") from exc

    sources = []
    for meta, doc in zip(metadatas, docs):
        sources.append(
            {
                "table": meta.get("source_table"),
                "record_id": meta.get("record_id"),
                "classification": meta.get("classification"),
                "snippet": doc[:220],
            }
        )

    coords = extract_coordinates_from_metadata(metadatas)

    return ChatResponse(response=model_response, sources=sources, coordinates=coords)
