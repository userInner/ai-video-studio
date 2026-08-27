from app.director import CodexDirector
from app.schemas import DiscoveryContract, ScriptContract


def test_demo_discovery_satisfies_contract() -> None:
    result = CodexDirector._demo_result("一个测试标题")
    validated = DiscoveryContract.model_validate(result)
    assert len(validated.options) == 3
    assert {option.label for option in validated.options} == {"认知反转", "利益相关", "人性透视"}


def test_script_schema_is_strict_structured_output_compatible() -> None:
    schema = ScriptContract.model_json_schema()

    def assert_all_object_properties_are_required(node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                assert set(node.get("required", [])) == set(node["properties"])
                assert node.get("additionalProperties") is False
            for value in node.values():
                assert_all_object_properties_are_required(value)
        elif isinstance(node, list):
            for value in node:
                assert_all_object_properties_are_required(value)

    assert_all_object_properties_are_required(schema)
