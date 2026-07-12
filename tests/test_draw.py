import sys
import os
import shutil
from pathlib import Path


def fix_relative_imports(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    content = content.replace("from .utils import", "from utils import")
    content = content.replace("from .models import", "from models import")
    content = content.replace("from .data_source import", "from data_source import")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)


def main():
    project_root = Path(__file__).parent.parent
    plugin_dir = project_root / "nonebot_plugin_steam_info"
    temp_dir = Path(__file__).parent / ".temp"
    output_dir = Path(__file__).parent / ".dist"

    temp_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    for filename in ["draw.py", "utils.py", "models.py", "data_source.py"]:
        src = plugin_dir / filename
        dst = temp_dir / filename
        if src.exists():
            shutil.copy2(src, dst)
            fix_relative_imports(dst)

    res_src = plugin_dir / "res"
    res_dst = temp_dir / "res"
    if res_src.exists() and not res_dst.exists():
        shutil.copytree(res_src, res_dst)

    sys.path.insert(0, str(temp_dir))

    from draw import (
        set_font_paths,
        check_font,
        draw_start_gaming,
        draw_stop_gaming,
        unknown_avatar_path,
    )

    set_font_paths(
        "fonts/MiSans-Regular.ttf",
        "fonts/MiSans-Light.ttf",
        "fonts/MiSans-Bold.ttf",
    )

    try:
        check_font()
        print("字体检查通过")
    except FileNotFoundError as e:
        print(f"字体检查失败: {e}")
        return

    from PIL import Image

    avatar = Image.open(unknown_avatar_path)

    test_cases = [
        {
            "name": "start_gaming",
            "func": draw_start_gaming,
            "args": [avatar, "测试玩家", "Counter-Strike 2", "昵称"],
        },
        {
            "name": "start_gaming_no_nickname",
            "func": draw_start_gaming,
            "args": [avatar, "TestPlayer", "Dota 2", None],
        },
        {
            "name": "stop_gaming_short",
            "func": draw_stop_gaming,
            "args": [avatar, "测试玩家", "Counter-Strike 2", "30 分钟", "昵称"],
        },
        {
            "name": "stop_gaming_long",
            "func": draw_stop_gaming,
            "args": [avatar, "TestPlayer", "Elden Ring", "2 小时 30 分钟", None],
        },
        {
            "name": "stop_gaming_long_name",
            "func": draw_stop_gaming,
            "args": [avatar, "测试玩家", "Counter-Strike 2: Global Offensive", "1 小时 15 分钟", "昵称"],
        },
    ]

    for case in test_cases:
        try:
            image = case["func"](*case["args"])
            output_path = output_dir / f"{case['name']}.png"
            image.save(output_path)
            print(f"已保存: {output_path}")
        except Exception as e:
            print(f"生成 {case['name']} 失败: {e}")

    print(f"\n测试完成！结果已保存到 {output_dir}")


if __name__ == "__main__":
    main()