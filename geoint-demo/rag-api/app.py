import json
import logging
import os
import re
import asyncio
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import boto3
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

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0")

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_SESSION_TOKEN = os.getenv("AWS_SESSION_TOKEN")


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
bedrock_client = None


def create_bedrock_client():
    kwargs = {"region_name": AWS_REGION}
    if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
        kwargs["aws_access_key_id"] = AWS_ACCESS_KEY_ID
        kwargs["aws_secret_access_key"] = AWS_SECRET_ACCESS_KEY
        if AWS_SESSION_TOKEN:
            kwargs["aws_session_token"] = AWS_SESSION_TOKEN
    return boto3.client("bedrock-runtime", **kwargs)


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


def extract_bedrock_text(payload: Dict[str, Any]) -> str:
    # Claude-style response format
    content = payload.get("content")
    if isinstance(content, list):
        text_chunks = [item.get("text", "") for item in content if isinstance(item, dict)]
        text = "".join(text_chunks).strip()
        if text:
            return text

    # Titan-style response format fallback
    results = payload.get("results")
    if isinstance(results, list) and results:
        first = results[0]
        if isinstance(first, dict):
            output_text = first.get("outputText")
            if isinstance(output_text, str) and output_text.strip():
                return output_text.strip()

    return "No response generated."


def invoke_bedrock(prompt: str) -> str:
    if bedrock_client is None:
        raise RuntimeError("Bedrock client is not initialized")

    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 700,
        "temperature": 0.2,
        "messages": [{"role": "user", "content": prompt}],
    }

    response = bedrock_client.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps(request_body),
        accept="application/json",
        contentType="application/json",
    )
    payload = json.loads(response["body"].read())
    return extract_bedrock_text(payload)


async def query_bedrock(prompt: str) -> str:
    return await asyncio.to_thread(invoke_bedrock, prompt)


@app.on_event("startup")
def startup_event() -> None:
    global bedrock_client

    try:
        bedrock_client = create_bedrock_client()
        logger.info("Initialized AWS Bedrock client in region %s", AWS_REGION)
        init_vector_store()
    except Exception as exc:
        logger.exception("Startup initialization failed: %s", exc)


@app.get("/api/health")
def health() -> Dict[str, str]:
    status = "ok" if chroma_collection is not None and embedding_model is not None and bedrock_client is not None else "degraded"
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
        model_response = await query_bedrock(prompt)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AWS Bedrock request failed: {exc}") from exc

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
