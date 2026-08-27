from app.director import CodexDirector
from app.schemas import AngleDiscoveryContract, DiscoveryContract, ScriptContract


def test_demo_discovery_satisfies_contract() -> None:
    result = CodexDirector._demo_result("一个测试标题")
    validated = DiscoveryContract.model_validate(result)
    assert len(validated.options) == 3
    assert {option.label for option in validated.options} == {"认知反转", "利益相关", "人性透视"}


def _assert_strict_structured_output_schema(node: object) -> None:
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            assert set(node.get("required", [])) == set(node["properties"])
            assert node.get("additionalProperties") is False
        for value in node.values():
            _assert_strict_structured_output_schema(value)
    elif isinstance(node, list):
        for value in node:
            _assert_strict_structured_output_schema(value)


def test_all_model_output_schemas_are_strict_compatible() -> None:
    for contract in (AngleDiscoveryContract, ScriptContract):
        _assert_strict_structured_output_schema(contract.model_json_schema())
