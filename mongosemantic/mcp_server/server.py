"""FastMCP server exposing semantic-search tools to AI agents.

The docstrings on each `@app.tool()` function are what Claude/other agents
see when they discover the tool. Keep them concrete and actionable — they
should help the model decide *when* to call the tool, not just *how*.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.mcp_server import tools as t


def create_mcp() -> FastMCP:
    app = FastMCP(
        name="mongosemantic",
        instructions=(
            "Tools for querying a MongoDB database by semantic meaning. Use "
            "semantic_search or search_all_collections to find documents by "
            "what they're about. Use safe_aggregation when you need exact "
            "filtering, counting, or grouping. Call list_configured first to "
            "see which collections are searchable."
        ),
    )

    def _open():
        s = Settings()
        return MongoConnection.open(s.uri, s.database)

    @app.tool()
    def list_collections() -> dict:
        """List every collection in the configured database.

        Returns each collection's name, whether semantic search is configured,
        the configured fields and embedding model, and the storage mode
        ("shadow" or "inline"). Call this before semantic_search to discover
        what's available.
        """
        conn = _open()
        try:
            return t.t_list_collections(conn.db)
        finally:
            conn.close()

    @app.tool()
    def list_configured() -> dict:
        """List only collections that have semantic search configured.

        Use this as a quick precheck before semantic_search: anything not in
        this list cannot be searched semantically (yet).
        """
        conn = _open()
        try:
            return t.t_list_configured(conn.db)
        finally:
            conn.close()

    @app.tool()
    def inspect_collection(name: str, sample: int = 500) -> dict:
        """Sample documents from a collection and score each field for
        semantic-search suitability.

        Returns one entry per field with type, coverage, average length, and
        a band ("great", "good", "usable", "not_recommended"). Useful when
        deciding which fields to configure for semantic search.
        """
        conn = _open()
        try:
            return t.t_inspect_collection(conn.db, name, sample=sample)
        finally:
            conn.close()

    @app.tool()
    def get_sample_documents(name: str, limit: int = 5) -> dict:
        """Fetch a small random sample of documents from a collection.

        Use this to see what the data actually looks like before forming a
        query. The internal embedding sub-document (`_msem`) is stripped from
        results — you'll only see the user's fields.
        """
        conn = _open()
        try:
            return t.t_get_sample_documents(conn.db, name, limit=limit)
        finally:
            conn.close()

    @app.tool()
    def get_status() -> dict:
        """Report deployment health: topology (atlas / replica_set / standalone),
        number of configured collections, total embedded vectors, and job-queue
        counts (pending, in_flight, completed, failed)."""
        conn = _open()
        try:
            return t.t_get_status(conn.db, conn.topology)
        finally:
            conn.close()

    @app.tool()
    def semantic_search(query: str, collection: str, limit: int = 10) -> dict:
        """Find documents in `collection` whose content matches `query` by
        meaning, not by keyword.

        Returns rows sorted by similarity score, each with the matched chunk
        of text and the source document. Use this when the user asks
        questions like "find articles about X" — it understands synonyms and
        paraphrases. For exact filtering, use safe_aggregation instead.
        """
        conn = _open()
        try:
            return t.t_semantic_search(
                conn.db, conn.topology, query, collection, limit=limit
            )
        finally:
            conn.close()

    @app.tool()
    def hybrid_search(query: str, collection: str, limit: int = 10) -> dict:
        """Find documents in `collection` by combining semantic similarity and
        keyword (BM25) matching, fused via Atlas `$rankFusion`.

        Use this when the query mixes meaning and specific terms — e.g.
        "MongoDB 7.0 replica set issues" benefits from both signals (semantic
        catches "replica set" → "replication", keyword anchors on "7.0").
        Requires Atlas + shadow-mode collections; falls back to pure
        semantic search with a notice otherwise.
        """
        conn = _open()
        try:
            return t.t_hybrid_search(
                conn.db, conn.topology, query, collection, limit=limit
            )
        finally:
            conn.close()

    @app.tool()
    def search_all_collections(query: str, limit: int = 10) -> dict:
        """Like semantic_search but fans out across every configured collection
        at once, then merges and ranks the combined results.

        Returns the top `limit` rows from any collection. Each row carries
        its `source_collection`. Useful when the user doesn't know which
        collection to look in.
        """
        conn = _open()
        try:
            return t.t_search_all_collections(
                conn.db, conn.topology, query, limit=limit
            )
        finally:
            conn.close()

    @app.tool()
    def migrate_model(collection: str, new_model: str) -> dict:
        """Switch an existing collection's embedding model with near-zero downtime.

        Builds new embeddings into a temporary shadow collection, then
        atomically renames it into place. The old shadow is preserved as
        `{name}_archive_{timestamp}` for rollback — drop it manually once
        you've verified the new model. Shadow-mode collections only;
        inline-mode is rejected.
        """
        conn = _open()
        try:
            return t.t_migrate_model(conn, collection, new_model)
        finally:
            conn.close()

    @app.tool()
    def safe_aggregation(name: str, pipeline: list[dict]) -> dict:
        """Run a read-only MongoDB aggregation pipeline against `name`.

        Use this for exact filtering, counting, grouping, or projections
        — anything semantic_search can't do precisely. The pipeline is
        validated before running: `$out`, `$merge`, `$function`,
        `$accumulator`, `$where`, and `$jsonSchema` are rejected. Hard caps:
        10s execution time, 100 result rows.

        Example: `[{"$match": {"category": "travel"}}, {"$limit": 20}]`.
        """
        conn = _open()
        try:
            return t.t_safe_aggregation(conn.db, name, pipeline)
        finally:
            conn.close()

    @app.tool()
    def get_schema_context(name: str, sample: int = 100) -> dict:
        """Return a compact schema description of `name`: one entry per field
        path with its inferred type, coverage across sampled docs, and an
        example value.

        Designed to be small enough to fit in a system prompt — call this
        once when you need to construct a safe_aggregation pipeline and
        don't know the shape of the documents.
        """
        conn = _open()
        try:
            return t.t_get_schema_context(conn.db, name, sample=sample)
        finally:
            conn.close()

    return app
