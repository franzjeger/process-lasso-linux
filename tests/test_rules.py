"""Tests for Rule matching/serialization and RuleEngine in rules.py."""
import pytest

from rules import Rule, RuleEngine


class TestRuleMatches:
    def test_contains_case_insensitive(self):
        rule = Rule(pattern="Steam", match_type="contains")
        assert rule.matches("steamwebhelper")
        assert rule.matches("STEAM")
        assert not rule.matches("firefox")

    def test_exact(self):
        rule = Rule(pattern="firefox", match_type="exact")
        assert rule.matches("firefox")
        assert not rule.matches("Firefox")       # exact is case-sensitive
        assert not rule.matches("firefox-bin")

    def test_regex(self):
        rule = Rule(pattern=r"^game.*\.exe$", match_type="regex")
        assert rule.matches("gamelauncher.exe")
        assert not rule.matches("mygame.exe")

    def test_regex_is_search_not_match(self):
        rule = Rule(pattern=r"\.exe", match_type="regex")
        assert rule.matches("attila.exe")

    def test_invalid_regex_returns_false(self):
        rule = Rule(pattern="[unclosed", match_type="regex")
        assert rule.matches("anything") is False

    def test_disabled_never_matches(self):
        rule = Rule(pattern="steam", match_type="contains", enabled=False)
        assert not rule.matches("steam")

    def test_empty_pattern_never_matches(self):
        rule = Rule(pattern="", match_type="contains")
        assert not rule.matches("anything")

    def test_unknown_match_type_falls_back_to_contains(self):
        rule = Rule(pattern="fox", match_type="glob")
        assert rule.matches("firefox")


class TestRuleSerialization:
    def test_round_trip(self):
        rule = Rule(name="Game", pattern=r"\.exe$", match_type="regex",
                    affinity="0-7", nice=-5, ionice_class=1, ionice_level=2,
                    enabled=False)
        restored = Rule.from_dict(rule.to_dict())
        assert restored == rule

    def test_from_dict_defaults_for_missing_keys(self):
        rule = Rule.from_dict({})
        assert rule.name == ""
        assert rule.pattern == ""
        assert rule.match_type == "contains"
        assert rule.affinity is None
        assert rule.nice is None
        assert rule.ionice_class is None
        assert rule.enabled is True
        assert rule.rule_id  # a uuid was generated

    def test_from_dict_generates_unique_ids(self):
        assert Rule.from_dict({}).rule_id != Rule.from_dict({}).rule_id

    def test_from_dict_preserves_existing_id(self):
        rule = Rule.from_dict({"rule_id": "my-id"})
        assert rule.rule_id == "my-id"


class TestRuleEngine:
    def test_load_and_get(self):
        engine = RuleEngine()
        engine.load_rules([{"name": "a", "pattern": "x"}, {"name": "b", "pattern": "y"}])
        assert [r.name for r in engine.get_rules()] == ["a", "b"]

    def test_get_rules_returns_copy(self):
        engine = RuleEngine()
        engine.add_rule(Rule(name="a", pattern="x"))
        engine.get_rules().clear()
        assert len(engine.get_rules()) == 1

    def test_add_remove(self):
        engine = RuleEngine()
        rule = Rule(name="a", pattern="x")
        engine.add_rule(rule)
        engine.remove_rule(rule.rule_id)
        assert engine.get_rules() == []

    def test_remove_unknown_id_is_noop(self):
        engine = RuleEngine()
        engine.add_rule(Rule(name="a", pattern="x"))
        engine.remove_rule("does-not-exist")
        assert len(engine.get_rules()) == 1

    def test_update_rule(self):
        engine = RuleEngine()
        rule = Rule(name="old", pattern="x")
        engine.add_rule(rule)
        updated = Rule(rule_id=rule.rule_id, name="new", pattern="y")
        engine.update_rule(updated)
        assert engine.get_rules()[0].name == "new"

    def test_update_unknown_rule_is_noop(self):
        engine = RuleEngine()
        engine.add_rule(Rule(name="a", pattern="x"))
        engine.update_rule(Rule(rule_id="ghost", name="b", pattern="y"))
        assert engine.get_rules()[0].name == "a"

    def test_to_dict_list_round_trip(self):
        engine = RuleEngine()
        engine.add_rule(Rule(name="a", pattern="x", nice=5))
        engine2 = RuleEngine()
        engine2.load_rules(engine.to_dict_list())
        assert engine2.get_rules() == engine.get_rules()


class TestApplyToProcess:
    @pytest.fixture
    def mock_utils(self, monkeypatch):
        import utils as real_utils
        import rules as rules_mod
        calls = {"affinity": [], "nice": [], "ionice": []}

        class FakeUtils:
            set_affinity_ok = True
            set_nice_ok = True
            set_ionice_ok = True
            # Current-state readbacks for drift checks. Defaults simulate a
            # process with nothing applied yet, so every rule action fires.
            current_nice = None
            current_ionice = None
            current_affinity = None
            online_cpus = set(range(32))

            cpulist_to_set = staticmethod(real_utils.cpulist_to_set)

            @staticmethod
            def get_online_cpus():
                return FakeUtils.online_cpus

            @staticmethod
            def get_nice(pid):
                return FakeUtils.current_nice

            @staticmethod
            def get_ionice(pid):
                return FakeUtils.current_ionice

            @staticmethod
            def get_affinity_set(pid):
                return FakeUtils.current_affinity

            @staticmethod
            def set_affinity(pid, cpulist):
                calls["affinity"].append((pid, cpulist))
                return FakeUtils.set_affinity_ok

            @staticmethod
            def set_nice(pid, nice):
                calls["nice"].append((pid, nice))
                return FakeUtils.set_nice_ok

            @staticmethod
            def set_ionice(pid, cls, level):
                calls["ionice"].append((pid, cls, level))
                return FakeUtils.set_ionice_ok

        monkeypatch.setattr(rules_mod, "utils", FakeUtils)
        FakeUtils.calls = calls
        return FakeUtils

    def test_applies_all_actions_of_matching_rule(self, mock_utils):
        engine = RuleEngine()
        engine.add_rule(Rule(name="r", pattern="game", affinity="0-7",
                             nice=-5, ionice_class=2, ionice_level=0))
        actions = engine.apply_to_process(42, "game.exe")
        assert mock_utils.calls["affinity"] == [(42, "0-7")]
        assert mock_utils.calls["nice"] == [(42, -5)]
        assert mock_utils.calls["ionice"] == [(42, 2, 0)]
        assert len(actions) == 3

    def test_non_matching_rule_does_nothing(self, mock_utils):
        engine = RuleEngine()
        engine.add_rule(Rule(name="r", pattern="game", affinity="0-7"))
        actions = engine.apply_to_process(42, "firefox")
        assert actions == []
        assert mock_utils.calls["affinity"] == []

    def test_none_fields_are_skipped(self, mock_utils):
        engine = RuleEngine()
        engine.add_rule(Rule(name="r", pattern="game", nice=5))
        engine.apply_to_process(42, "game")
        assert mock_utils.calls["affinity"] == []
        assert mock_utils.calls["ionice"] == []
        assert mock_utils.calls["nice"] == [(42, 5)]

    def test_nice_zero_is_applied(self, mock_utils):
        # nice=0 is a valid value distinct from "not set" (None)
        engine = RuleEngine()
        engine.add_rule(Rule(name="r", pattern="game", nice=0))
        engine.apply_to_process(42, "game")
        assert mock_utils.calls["nice"] == [(42, 0)]

    def test_failed_nice_still_reports_action(self, mock_utils):
        mock_utils.set_nice_ok = False
        engine = RuleEngine()
        engine.add_rule(Rule(name="r", pattern="game", nice=-10))
        actions = engine.apply_to_process(42, "game")
        assert len(actions) == 1
        assert "failed" in actions[0]

    def test_failed_affinity_reports_nothing(self, mock_utils):
        mock_utils.set_affinity_ok = False
        engine = RuleEngine()
        engine.add_rule(Rule(name="r", pattern="game", affinity="0-7"))
        assert engine.apply_to_process(42, "game") == []

    def test_multiple_matching_rules_all_apply(self, mock_utils):
        engine = RuleEngine()
        engine.add_rule(Rule(name="a", pattern="game", nice=5))
        engine.add_rule(Rule(name="b", pattern=".exe", nice=10))
        engine.apply_to_process(42, "game.exe")
        assert mock_utils.calls["nice"] == [(42, 5), (42, 10)]

    def test_already_applied_nice_is_skipped(self, mock_utils):
        mock_utils.current_nice = 5
        engine = RuleEngine()
        engine.add_rule(Rule(name="r", pattern="game", nice=5))
        assert engine.apply_to_process(42, "game") == []
        assert mock_utils.calls["nice"] == []

    def test_already_applied_affinity_is_skipped(self, mock_utils):
        mock_utils.current_affinity = {0, 1, 2, 3}
        engine = RuleEngine()
        engine.add_rule(Rule(name="r", pattern="game", affinity="0-3"))
        assert engine.apply_to_process(42, "game") == []

    def test_affinity_comparison_ignores_offline_cpus(self, mock_utils):
        # Rule wants 0-7 but 4-7 are parked: running on 0-3 counts as applied
        mock_utils.online_cpus = {0, 1, 2, 3}
        mock_utils.current_affinity = {0, 1, 2, 3}
        engine = RuleEngine()
        engine.add_rule(Rule(name="r", pattern="game", affinity="0-7"))
        assert engine.apply_to_process(42, "game") == []

    def test_drifted_affinity_is_reapplied(self, mock_utils):
        mock_utils.current_affinity = {0, 1}
        engine = RuleEngine()
        engine.add_rule(Rule(name="r", pattern="game", affinity="0-3"))
        engine.apply_to_process(42, "game")
        assert mock_utils.calls["affinity"] == [(42, "0-3")]

    def test_already_applied_ionice_is_skipped(self, mock_utils):
        mock_utils.current_ionice = (2, 4)
        engine = RuleEngine()
        engine.add_rule(Rule(name="r", pattern="game", ionice_class=2, ionice_level=4))
        assert engine.apply_to_process(42, "game") == []

    def test_ionice_level_ignored_for_idle_class(self, mock_utils):
        # idle class has no level; matching class alone counts as applied
        mock_utils.current_ionice = (3, 0)
        engine = RuleEngine()
        engine.add_rule(Rule(name="r", pattern="game", ionice_class=3, ionice_level=5))
        assert engine.apply_to_process(42, "game") == []

    def test_skip_nice_leaves_nice_alone(self, mock_utils):
        engine = RuleEngine()
        engine.add_rule(Rule(name="r", pattern="game", nice=0, affinity="0-3"))
        engine.apply_to_process(42, "game", skip_nice=True)
        assert mock_utils.calls["nice"] == []
        assert mock_utils.calls["affinity"] == [(42, "0-3")]

    def test_has_match(self, mock_utils):
        engine = RuleEngine()
        engine.add_rule(Rule(name="r", pattern="game", nice=5))
        assert engine.has_match("game.exe") is True
        assert engine.has_match("firefox") is False

    def test_has_match_ignores_disabled_rules(self, mock_utils):
        engine = RuleEngine()
        engine.add_rule(Rule(name="r", pattern="game", nice=5, enabled=False))
        assert engine.has_match("game") is False

    def test_log_callback_receives_actions(self, mock_utils):
        engine = RuleEngine()
        logged = []
        engine.set_log_callback(logged.append)
        engine.add_rule(Rule(name="r", pattern="game", nice=5))
        actions = engine.apply_to_process(42, "game")
        assert logged == actions
