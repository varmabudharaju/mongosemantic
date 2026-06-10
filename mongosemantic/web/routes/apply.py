from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field

from mongosemantic.config import MODEL_DIMS, Settings
from mongosemantic.db.client import MongoConnection, Topology
from mongosemantic.db.indexes import (
    create_atlas_vector_index,
    ensure_shadow_indexes,
    shadow_collection_name,
    suggested_atlas_command,
)
from mongosemantic.db.queries import inline_embedding_path
from mongosemantic.search.local_hybrid import ensure_text_index
from mongosemantic.state import (
    CollectionConfig,
    FieldSpec,
    ensure_indexes,
    save_config,
)
from mongosemantic.web.identifiers import IdentifierError, validate_identifier

router = APIRouter()


class ApplyRequest(BaseModel):
    fields: list[str] = Field(..., min_length=1)
    mode: Literal["shadow", "inline"] = "shadow"
    chunked: bool = False
    chunk_size: int = Field(512, ge=64, le=2048)
    chunk_overlap: int = Field(64, ge=0, le=256)
    model: str = "local-fast"


@router.post("/api/collections/{name}/apply")
def apply(name: str = Path(...), req: ApplyRequest = ...) -> dict:
    try:
        validate_identifier(name)
        for f in req.fields:
            validate_identifier(f)
    except IdentifierError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if req.model not in MODEL_DIMS:
        raise HTTPException(status_code=400, detail=f"unknown model: {req.model}")

    if req.chunked and req.mode == "inline":
        raise HTTPException(
            status_code=400,
            detail="Chunked embeddings require shadow mode. Switch mode to shadow or turn off chunking.",
        )

    settings = Settings.from_environment()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        db = conn.db
        ensure_indexes(db)
        now = datetime.now(timezone.utc)
        dim = MODEL_DIMS[req.model]
        field_specs = [
            FieldSpec(path=p, chunked=req.chunked,
                      chunk_size=req.chunk_size, chunk_overlap=req.chunk_overlap)
            for p in req.fields
        ]
        shadow_name: str | None
        if req.mode == "shadow":
            shadow_name = shadow_collection_name(name)
            ensure_shadow_indexes(db[shadow_name])
            ensure_text_index(db[shadow_name])
        else:
            shadow_name = None
        cfg = CollectionConfig(
            collection=name,
            mode=req.mode,
            shadow_collection=shadow_name,
            fields=field_specs,
            embedding_model=req.model,
            embedding_dim=dim,
            created_at=now,
            updated_at=now,
        )
        save_config(db, cfg)

        atlas_action: dict | None = None
        if conn.topology == Topology.ATLAS:
            try:
                created: list[str] = []
                for p in req.fields:
                    if req.mode == "shadow":
                        created.append(
                            create_atlas_vector_index(
                                db[shadow_name], name, p, dim, path="embedding"
                            )
                        )
                    else:
                        created.append(
                            create_atlas_vector_index(
                                db[name], name, p, dim,
                                path=inline_embedding_path(p),
                            )
                        )
                atlas_action = {"status": "created", "names": created}
            except Exception as e:
                cmds: list[str] = []
                for p in req.fields:
                    if req.mode == "shadow":
                        cmds.append(suggested_atlas_command(name, p, shadow_name, dim))
                    else:
                        cmds.append(
                            suggested_atlas_command(
                                name, p, name, dim, path=inline_embedding_path(p)
                            )
                        )
                atlas_action = {
                    "status": "manual_required",
                    "error": str(e),
                    "commands": cmds,
                }
        return {
            "ok": True,
            "topology": conn.topology.value,
            "mode": req.mode,
            "shadow_collection": shadow_name,
            "atlas": atlas_action,
        }
    finally:
        conn.close()
