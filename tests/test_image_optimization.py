from pathlib import Path

from PIL import Image

from llmpdf.image_analysis_task import _optimize_vision_images
from llmpdf.models import PipelineConfig


def test_large_vision_image_uses_smaller_webp_without_resizing(tmp_path: Path) -> None:
    config = PipelineConfig(pdf=tmp_path / "input.pdf", output_dir=tmp_path / "out")
    source = config.assets_dir / "images" / "image-0001.png"
    source.parent.mkdir(parents=True)
    image = Image.effect_noise((1800, 1800), 80).convert("RGB")
    image.save(source, "PNG")
    assert source.stat().st_size > 1024 * 1024
    original_bytes = source.stat().st_size
    records = [
        {
            "id": "image-0001",
            "source": "vision",
            "image": "assets/images/image-0001.png",
        }
    ]
    _optimize_vision_images(config, records)
    optimized = config.output_dir / records[0]["image"]
    assert optimized.suffix == ".webp"
    assert optimized.stat().st_size < original_bytes
    assert not source.exists()
    with Image.open(optimized) as opened:
        assert opened.size == (1800, 1800)
    assert records[0]["image_optimization"]["resolution_preserved"] is True
