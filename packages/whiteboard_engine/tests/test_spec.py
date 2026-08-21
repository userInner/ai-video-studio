from whiteboard_engine import RenderSpec


def test_vertical_render_spec() -> None:
    spec = RenderSpec()
    spec.validate()
    assert spec.height > spec.width
