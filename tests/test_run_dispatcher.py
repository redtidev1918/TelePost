"""
多 bot 启动器（run.py）测试
"""
import run


class TestBotIndices:
    def test_detects_bots(self):
        env = {"BOT1_TOKEN": "t1", "BOT2_TOKEN": "t2"}
        assert run.bot_indices(env) == [1, 2]

    def test_gap_stops_detection(self):
        # BOT1 存在而 BOT2 缺失时只认 BOT1（不跳号）
        env = {"BOT1_TOKEN": "t1", "BOT3_TOKEN": "t3"}
        assert run.bot_indices(env) == [1]

    def test_no_bots(self):
        assert run.bot_indices({"TOKEN": "t"}) == []


class TestBuildBotEnv:
    def base(self):
        return {
            "BOT1_TOKEN": "t1", "BOT1_CHANNEL_ID": "@c1", "BOT1_OWNER_ID": "111",
            "BOT2_TOKEN": "t2", "BOT2_CHANNEL_ID": "@c2", "BOT2_OWNER_ID": "222",
            "TZ": "Asia/Shanghai",
        }

    def test_maps_prefixed_vars(self):
        env = run.build_bot_env(1, self.base())
        assert env["TOKEN"] == "t1"
        assert env["CHANNEL_ID"] == "@c1"
        assert env["OWNER_ID"] == "111"
        assert env["DB_PATH"] == "data/bot1/submissions.db"
        assert env["SEARCH_INDEX_DIR"] == "data/bot1/search_index"
        assert env["HEALTH_PORT"] == "8080"

    def test_bot2_isolated(self):
        env = run.build_bot_env(2, self.base())
        assert env["TOKEN"] == "t2"
        assert env["CHANNEL_ID"] == "@c2"
        assert env["OWNER_ID"] == "222"
        assert env["DB_PATH"] == "data/bot2/submissions.db"
        assert env["HEALTH_PORT"] == "8081"

    def test_strips_other_bot_tokens(self):
        env = run.build_bot_env(1, self.base())
        assert "BOT2_TOKEN" not in env
        assert "BOT1_TOKEN" not in env
        assert "TOKEN" in env

    def test_respects_overrides(self):
        env = self.base()
        env["BOT2_DB_PATH"] = "data/custom.db"
        env["BOT2_SEARCH_ENABLED"] = "false"
        out = run.build_bot_env(2, env)
        assert out["DB_PATH"] == "data/custom.db"
        assert out["SEARCH_ENABLED"] == "false"

    def test_base_env_not_mutated(self):
        base = self.base()
        run.build_bot_env(1, base)
        assert "TOKEN" not in base  # 原环境不被污染
