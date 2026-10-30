from leadbot.tools.knowledge_base import KnowledgeBaseTool, load_case_studies


def test_loads_the_three_case_studies():
    names = {c.name for c in load_case_studies()}
    assert names == {"tiendah2r", "luxtimee", "tiendamotek"}


def test_tool_filters_by_sector():
    result = KnowledgeBaseTool()(sector="repuestos_autopartes")
    assert result.ok
    assert set(result.data["cases"]) == {"tiendah2r", "tiendamotek"}


def test_tool_reports_bad_args_instead_of_raising():
    result = KnowledgeBaseTool()(sector=123)
    assert not result.ok
