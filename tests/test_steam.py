"""Profile parser regression tests without starting the NoneBot plugin."""

import importlib.util
import importlib
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType
import unittest
from unittest.mock import AsyncMock, patch

import httpx


# Load the parser with its real dependencies, bypassing plugin registration.
PLUGIN_DIR = Path(__file__).resolve().parents[1] / "nonebot_plugin_steam_info"
PACKAGE_NAME = "_steam_info_parser_tests"
package = ModuleType(PACKAGE_NAME)
package.__path__ = [str(PLUGIN_DIR)]
sys.modules[PACKAGE_NAME] = package
spec = importlib.util.spec_from_file_location(
    f"{PACKAGE_NAME}.steam", PLUGIN_DIR / "steam.py"
)
steam = importlib.util.module_from_spec(spec)
spec.loader.exec_module(steam)
utils = importlib.import_module(f"{PACKAGE_NAME}.utils")


def recent_game(name, summary):
    summary_html = (
        f'<span class="game_info_achievement_summary">{summary}</span>'
        if summary is not None
        else ""
    )
    return f"""
        <div class="recent_game">
            <div class="game_name">{name}</div>
            <img class="game_capsule" src="https://example.com/123/capsule.jpg">
            <div class="game_info_details">总时数 12.5 小时</div>
            {summary_html}
        </div>
    """


class GetUserDataTests(unittest.IsolatedAsyncioTestCase):
    async def get_games(self, summary):
        html = "<title>Steam 社区 :: 测试玩家</title>"
        html += recent_game("First game", summary)
        html += recent_game("Second game", '<span class="ellipsis">2 / 10</span>')
        response = httpx.Response(
            200, text=html, request=httpx.Request("GET", "https://steamcommunity.com")
        )
        with TemporaryDirectory() as cache_dir:
            with patch.object(
                httpx.AsyncClient, "get", AsyncMock(return_value=response)
            ):
                with patch.object(steam, "_fetch", AsyncMock(return_value=b"image")):
                    result = await steam.get_user_data(
                        76561197960265729, Path(cache_dir)
                    )

        self.assertEqual(result["player_name"], "测试玩家")
        games = result["game_data"]
        self.assertEqual(
            [game["game_name"] for game in games], ["First game", "Second game"]
        )
        self.assertEqual(games[0]["play_time"], "12.5")
        self.assertEqual(games[0]["game_image"], b"image")
        self.assertEqual(games[1]["completed_achievement_number"], 2)
        self.assertEqual(games[1]["total_achievement_number"], 10)
        return games

    async def test_supported_achievement_summary_markup(self):
        cases = [
            ('<span class="ellipsis">3 / 12</span>', (3, 12)),
            ("3 / 12", (3, 12)),
            ("<span>3</span> / <span>12</span>", (3, 12)),
            ("已解锁 3 / 12 项成就", (3, 12)),
            ("1,234 / 2,345", (1234, 2345)),
            ("0 / 12", (0, 12)),
        ]
        for summary, expected in cases:
            with self.subTest(summary=summary):
                game = (await self.get_games(summary))[0]
                self.assertEqual(game["completed_achievement_number"], expected[0])
                self.assertEqual(game["total_achievement_number"], expected[1])

    async def test_missing_or_invalid_counts_preserve_games(self):
        for summary in [None, "", "尚无成就", "3 / ?", "0 / 0", "13 / 12"]:
            with self.subTest(summary=summary):
                game = (await self.get_games(summary))[0]
                self.assertNotIn("completed_achievement_number", game)
                self.assertNotIn("total_achievement_number", game)

    async def test_missing_game_elements_preserve_other_games(self):
        cases = [
            ('<div class="game_name">First game</div>', "", "Unknown"),
            (
                '<img class="game_capsule" src="https://example.com/123/capsule.jpg">',
                "",
                "First game",
            ),
            ('src="https://example.com/123/capsule.jpg"', "", "First game"),
            ('<div class="game_info_details">总时数 12.5 小时</div>', "", "First game"),
        ]
        for old, new, expected_name in cases:
            with self.subTest(element=old):
                html = recent_game("First game", None).replace(old, new)
                html += recent_game("Second game", None)
                response = httpx.Response(
                    200,
                    text=html,
                    request=httpx.Request("GET", "https://steamcommunity.com"),
                )
                with patch.object(
                    httpx.AsyncClient, "get", AsyncMock(return_value=response)
                ):
                    result = await steam.get_user_data(1, None)
                self.assertEqual(len(result["game_data"]), 2)
                game = result["game_data"][0]
                self.assertEqual(game["game_name"], expected_name)
                if "game_capsule" in old or old.startswith("src="):
                    self.assertEqual(
                        game["game_image"],
                        (PLUGIN_DIR / "res/default_header_image.jpg").read_bytes(),
                    )
                if "game_info_details" in old:
                    self.assertEqual(game["play_time"], "")
                    self.assertEqual(game["last_played"], "最后运行日期未知")

    async def test_missing_achievement_attributes_use_defaults(self):
        for markup in [
            '<div class="game_info_achievement"></div>',
            '<div class="game_info_achievement"><img></div>',
            '<div class="game_info_achievement" data-tooltip-text=""><img src=""></div>',
        ]:
            with self.subTest(markup=markup):
                html = f'<div class="recent_game">{markup}</div>' + recent_game(
                    "Second game", None
                )
                response = httpx.Response(
                    200,
                    text=html,
                    request=httpx.Request("GET", "https://steamcommunity.com"),
                )
                with patch.object(
                    httpx.AsyncClient, "get", AsyncMock(return_value=response)
                ):
                    result = await steam.get_user_data(1, None)
                achievement = result["game_data"][0]["achievements"][0]
                self.assertEqual(achievement["name"], "Unknown")
                self.assertEqual(
                    achievement["image"],
                    (PLUGIN_DIR / "res/default_achievement_image.png").read_bytes(),
                )
                self.assertEqual(len(result["game_data"]), 2)

    async def test_short_image_urls_and_optional_cache(self):
        html = '<link rel="image_src" href="avatar">'
        html += """<div class="recent_game">
            <img class="game_capsule" src="capsule">
            <div class="game_info_achievement"><img src="achievement"></div>
        </div>"""
        for use_cache in [False, True]:
            with self.subTest(use_cache=use_cache), TemporaryDirectory() as cache_dir:
                response = httpx.Response(
                    200,
                    text=html,
                    request=httpx.Request("GET", "https://steamcommunity.com"),
                )
                image_response = httpx.Response(200, content=b"image")
                with patch.object(
                    httpx.AsyncClient,
                    "get",
                    AsyncMock(
                        side_effect=[
                            response,
                            image_response,
                            image_response,
                            image_response,
                        ]
                    ),
                ):
                    result = await steam.get_user_data(
                        1, Path(cache_dir) if use_cache else None
                    )
                self.assertEqual(result["avatar"], b"image")
                self.assertEqual(result["game_data"][0]["game_image"], b"image")
                self.assertEqual(
                    result["game_data"][0]["achievements"][0]["image"], b"image"
                )
                self.assertEqual(list(Path(cache_dir).iterdir()), [])

    async def test_existing_cache_names_are_reused(self):
        html = """<link rel="image_src" href="https://avatars.example.com/abc_full.jpg">
            <div class="recent_game">
                <img class="game_capsule" src="https://images.example.com/apps/123/capsule.jpg?t=1">
                <div class="game_info_achievement">
                    <img src="https://images.example.com/123/achievement.jpg">
                </div>
            </div>"""
        with TemporaryDirectory() as directory:
            cache = Path(directory)
            (cache / "avatar_abc.jpg").write_bytes(b"cached avatar")
            (cache / "header_123.jpg").write_bytes(b"cached header")
            (cache / "achievement_123_achievement.jpg").write_bytes(
                b"cached achievement"
            )
            response = httpx.Response(
                200,
                text=html,
                request=httpx.Request("GET", "https://steamcommunity.com"),
            )
            with patch.object(
                httpx.AsyncClient, "get", AsyncMock(return_value=response)
            ) as get:
                result = await steam.get_user_data(1, cache)
            get.assert_awaited_once()
            self.assertEqual(result["avatar"], b"cached avatar")
            self.assertEqual(result["game_data"][0]["game_image"], b"cached header")
            self.assertEqual(
                result["game_data"][0]["achievements"][0]["image"],
                b"cached achievement",
            )

    def test_abnormal_urls_skip_cache(self):
        urls = [
            "",
            "capsule",
            "https://example.com",
            "https://example.com/",
            "https://example.com/..",
            "https://example.com/../image.jpg",
            "https://example.com/123/",
            "https://[",
            "file:///123/image.jpg",
            "https://example.com/123/image\0.jpg",
            "https://example.com/123/a\\b.jpg",
        ]
        for url in urls:
            for prefix in ["header", "achievement"]:
                with self.subTest(url=url, prefix=prefix):
                    self.assertIsNone(
                        steam._image_cache_file(Path("/cache"), prefix, url)
                    )

    async def test_http_errors_return_default_profile(self):
        for status in [302, 403, 500]:
            with self.subTest(status=status):
                response = httpx.Response(
                    status, request=httpx.Request("GET", "https://steamcommunity.com")
                )
                with patch.object(
                    httpx.AsyncClient, "get", AsyncMock(return_value=response)
                ):
                    result = await steam.get_user_data(1, None)
                self.assertEqual(result["steamid"], "1")
                self.assertEqual(result["game_data"], [])

    async def test_relative_redirect_is_followed(self):
        def respond(request):
            if request.url.path == "/profiles/1":
                return httpx.Response(302, headers={"Location": "/id/test"})
            return httpx.Response(200, text="<title>Steam 社区 :: Redirected</title>")

        real_client = httpx.AsyncClient

        def client(**kwargs):
            return real_client(transport=httpx.MockTransport(respond), **kwargs)

        with patch.object(httpx, "AsyncClient", side_effect=client):
            result = await steam.get_user_data(1, None)
        self.assertEqual(result["player_name"], "Redirected")


class PlayerSummariesTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_payloads_try_next_key(self):
        invalid = [
            None,
            [],
            {},
            {"response": None},
            {"response": []},
            {"response": {}},
            {"response": {"players": None}},
            {"response": {"players": {}}},
        ]
        good = {
            "response": {
                "players": [
                    {"steamid": "1", "personaname": "Player", "personastate": 1}
                ]
            }
        }
        for payload in invalid:
            with self.subTest(payload=payload):
                responses = [
                    httpx.Response(200, json=payload),
                    httpx.Response(200, json=good),
                ]
                with patch.object(
                    httpx.AsyncClient, "get", AsyncMock(side_effect=responses)
                ) as get:
                    result = await steam.get_steam_users_info(
                        ["1"], ["first", "second"]
                    )
                self.assertEqual(result, good)
                self.assertEqual(get.await_count, 2)

    async def test_missing_fields_and_invalid_entries(self):
        payload = {
            "response": {
                "players": [
                    None,
                    [],
                    {},
                    {"personaname": "No ID"},
                    {"steamid": "1"},
                    {"steamid": "2", "personaname": None, "personastate": None},
                    {
                        "steamid": "3",
                        "personaname": "Player",
                        "personastate": 1,
                        "gameextrainfo": "Game",
                    },
                ]
            }
        }
        with patch.object(
            httpx.AsyncClient,
            "get",
            AsyncMock(return_value=httpx.Response(200, json=payload)),
        ):
            result = await steam.get_steam_users_info(["1", "2", "3"], ["key"])
        players = result["response"]["players"]
        self.assertEqual([p["steamid"] for p in players], ["1", "2", "3"])
        for player in players[:2]:
            self.assertEqual(player["personaname"], "Unknown")
            self.assertEqual(player["personastate"], -1)
        self.assertEqual(players[2]["gameextrainfo"], "Game")

    async def test_invalid_json_returns_empty_players(self):
        with patch.object(
            httpx.AsyncClient,
            "get",
            AsyncMock(return_value=httpx.Response(200, text="not JSON")),
        ):
            result = await steam.get_steam_users_info(["1"], ["key"])
        self.assertEqual(result, {"response": {"players": []}})


class PlayerUtilitiesTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_avatar_uses_default_without_request(self):
        with patch.object(utils, "_fetch_avatar", AsyncMock()) as fetch:
            avatar = await utils.fetch_avatar({"steamid": "1"}, None)
        fetch.assert_not_awaited()
        self.assertGreater(avatar.width, 0)
        avatar.close()

    async def test_missing_hash_fetches_without_cache(self):
        for field in ["avatarfull", "avatarmedium", "avatar"]:
            with self.subTest(field=field), TemporaryDirectory() as cache_dir:
                with patch.object(
                    utils, "_fetch_avatar", AsyncMock(return_value="image")
                ) as fetch:
                    avatar = await utils.fetch_avatar(
                        {"steamid": "1", field: "https://example.com/avatar"},
                        Path(cache_dir),
                    )
                self.assertEqual(avatar, "image")
                fetch.assert_awaited_once_with("https://example.com/avatar", None)
                self.assertEqual(list(Path(cache_dir).iterdir()), [])

    async def test_missing_name_and_status_are_unknown(self):
        with patch.object(utils, "fetch_avatar", AsyncMock(return_value="image")):
            result = await utils.simplize_steam_player_data({"steamid": "1"})
        self.assertEqual(result["name"], "Unknown")
        self.assertEqual(result["status"], "未知")
        self.assertEqual(result["personastate"], -1)

    def test_missing_binding_or_nickname(self):
        from unittest.mock import Mock

        for binding in [None, {}, {"nickname": None}, {"nickname": "昵称"}]:
            with self.subTest(binding=binding):
                store = Mock()
                store.get_by_steam_id.return_value = binding
                result = utils.convert_player_name_to_nickname(
                    {"steamid": "1"}, "group", store
                )
                self.assertEqual(
                    result["nickname"], binding.get("nickname") if binding else None
                )


class CachedPlayerTests(unittest.TestCase):
    def test_missing_game_start_time_initializes_tracking(self):
        source = importlib.import_module(f"{PACKAGE_NAME}.data_source")
        for old in [
            {"steamid": "1", "gameextrainfo": "Game"},
            {"steamid": "1", "gameextrainfo": "Game", "game_start_time": None},
        ]:
            with self.subTest(old=old), TemporaryDirectory() as directory:
                store = source.SteamInfoData(Path(directory) / "players.json")
                store.content = [old]
                with patch.object(source.time, "time", return_value=123):
                    store.update_by_players([{"steamid": "1", "gameextrainfo": "Game"}])
                self.assertEqual(store.content[0]["game_start_time"], 123)


class MissingStatusDrawingTests(unittest.TestCase):
    def test_unknown_player_is_rendered(self):
        draw = importlib.import_module(f"{PACKAGE_NAME}.draw")
        from PIL import Image

        root = PLUGIN_DIR.parent
        draw.set_font_paths(
            root / "fonts/MiSans-Regular.ttf",
            root / "fonts/MiSans-Light.ttf",
            root / "fonts/MiSans-Bold.ttf",
        )
        avatar = Image.new("RGB", (64, 64))
        data = [
            {
                "avatar": avatar,
                "name": "Unknown",
                "status": "未知",
                "personastate": -1,
                "nickname": None,
            }
        ]
        empty = draw.draw_friends_status(avatar, "Group", [])
        image = draw.draw_friends_status(avatar, "Group", data)
        self.assertEqual(image.width, empty.width)
        self.assertGreater(image.height, empty.height)


class MissingStartTimeBroadcastTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_start_time_still_sends_broadcast(self):
        # Exercise the actual handler without registering or starting the bot.
        import ast
        import __future__
        import time
        from types import SimpleNamespace
        from unittest.mock import Mock

        tree = ast.parse((PLUGIN_DIR / "__init__.py").read_text())
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "broadcast_steam_info"
        )
        module = ast.Module(body=[function], type_ignores=[])
        for entry_type in ["stop", "change"]:
            for start_fields in [{}, {"game_start_time": None}]:
                with self.subTest(entry_type=entry_type, start_fields=start_fields):
                    old = {"steamid": "1", "gameextrainfo": "Old game", **start_fields}
                    player = {
                        "steamid": "1",
                        "personaname": "Player",
                        "gameextrainfo": "New game",
                    }
                    entry = {"type": entry_type, "player": player, "old_player": old}
                    message = Mock()
                    message.send = AsyncMock()
                    make_message = Mock(return_value=message)
                    namespace = {
                        "nonebot": SimpleNamespace(
                            get_bot=lambda: SimpleNamespace(
                                adapter=SimpleNamespace(get_name=lambda: "test")
                            )
                        ),
                        "disable_parent_data": SimpleNamespace(
                            is_disabled=lambda _: False
                        ),
                        "steam_info_data": SimpleNamespace(compare=lambda *_: [entry]),
                        "config": SimpleNamespace(steam_broadcast_type="none"),
                        "time": time,
                        "UniMessage": make_message,
                        "Text": lambda text: text,
                        "Target": lambda *args: args,
                    }
                    exec(
                        compile(
                            module,
                            str(PLUGIN_DIR / "__init__.py"),
                            "exec",
                            flags=__future__.annotations.compiler_flag,
                        ),
                        namespace,
                    )
                    await namespace["broadcast_steam_info"]("group", [old], [player])
                    message.send.assert_awaited_once()
                    self.assertIn("未知时长", make_message.call_args.args[0][0])


if __name__ == "__main__":
    unittest.main()
