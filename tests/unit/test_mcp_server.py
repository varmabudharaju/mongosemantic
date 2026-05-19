"""Smoke tests for the FastMCP wrapper itself — does it boot, register the
expected tools, and expose JSON schemas we can hand to a client."""
import asyncio


def test_create_mcp_registers_all_ten_tools():
    from mongosemantic.mcp_server import create_mcp
    app = create_mcp()
    tools = asyncio.run(app.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "list_collections",
        "list_configured",
        "inspect_collection",
        "get_sample_documents",
        "get_status",
        "semantic_search",
        "hybrid_search",
        "search_all_collections",
        "safe_aggregation",
        "get_schema_context",
    }


def test_each_tool_has_a_useful_description():
    from mongosemantic.mcp_server import create_mcp
    app = create_mcp()
    tools = asyncio.run(app.list_tools())
    for t in tools:
        # Description shouldn't be empty or just the function name.
        assert t.description and len(t.description) > 40, t.name


def test_semantic_search_advertises_required_args():
    from mongosemantic.mcp_server import create_mcp
    app = create_mcp()
    tools = asyncio.run(app.list_tools())
    sem = next(t for t in tools if t.name == "semantic_search")
    props = sem.inputSchema.get("properties", {})
    assert {"query", "collection", "limit"} <= set(props)
    required = sem.inputSchema.get("required", [])
    assert "query" in required and "collection" in required
