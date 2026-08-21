from app.director import CodexDirector
from app.schemas import DiscoveryContract


def test_demo_discovery_satisfies_contract() -> None:
    result = CodexDirector._demo_result("一个测试标题")
    validated = DiscoveryContract.model_validate(result)
    assert len(validated.options) == 3
    assert {option.label for option in validated.options} == {"认知反转", "利益相关", "人性透视"}
